// The executable inside "Reachy Meetings.app" (built by build_app.sh): runs reachy_meetings_app with Python inside
// this very process.
//
// Why not a shell script that starts Python: macOS decides by the app's own program who is asking for the
// microphone, the camera, the local network or a folder in Documents. With a script in its place it never asked
// at all and just refused (the project folder in Documents could not be read, 2026-11-13); and a Python started
// with exec would have asked as "python3.12". With Python inside the app's program, the questions name
// "Reachy Meetings", and gateway and backend, which the app starts, come under the same answers.
//
// Two ways to build it:
//   -DSTANDALONE            the app brings everything: Python in Contents/Resources/python, our code in
//                           Contents/Resources/code (laid out like the repository), found from where the app is
//   -DREPO=… -DPYTHON=…     a thin shell around a checkout and the backend's Python environment (for development)
// PYTHON is also what the app starts its parts with (sys.executable).

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <errno.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void alert(const char *message) {
    char command[2048];
    snprintf(command, sizeof command,
             "/usr/bin/osascript -e 'display alert \"Reachy Meetings cannot start\" message \"%s\"' >/dev/null 2>&1", message);
    system(command);
}

int main(int argc, char **argv) {
    static char python[PATH_MAX], code[PATH_MAX], entry[PATH_MAX];
#ifdef STANDALONE
    char raw[PATH_MAX], exe[PATH_MAX];
    uint32_t size = sizeof raw;
    if (_NSGetExecutablePath(raw, &size) != 0 || realpath(raw, exe) == NULL) {
        alert("It could not find out where it is.");
        return 1;
    }
    char *contents = dirname(dirname(exe));   // …/Contents/MacOS/reachy-meetings -> …/Contents
    snprintf(python, sizeof python, "%s/Resources/python/bin/python3", contents);
    snprintf(code, sizeof code, "%s/Resources/code", contents);
    static char bin[PATH_MAX], pyc[PATH_MAX];
    snprintf(bin, sizeof bin, "%s/Resources/bin", contents);
    setenv("REACHY_STANDALONE", "1", 1);
    setenv("REACHY_BUNDLE_BIN", bin, 1);
    // Nothing is written into the app itself (it is sealed by its signature): compiled Python goes to the caches
    snprintf(pyc, sizeof pyc, "%s/Library/Caches/Reachy Meetings/pyc", getenv("HOME") ? getenv("HOME") : "/tmp");
    setenv("PYTHONPYCACHEPREFIX", pyc, 1);
#else
    snprintf(python, sizeof python, "%s", PYTHON);
    snprintf(code, sizeof code, "%s", REPO);
#endif
    // The first look at our code: for a checkout in Documents, Desktop or Downloads, macOS asks the user here
    snprintf(entry, sizeof entry, "%s/laptop-app/reachy_meetings_app/__main__.py", code);
    if (access(entry, R_OK) != 0) {
        alert(errno == ENOENT
              ? "Its program files are missing. Build the app again with laptop-app/build_app.sh."
              : "It may not read its project folder. Allow it in System Settings > Privacy & Security > "
                "Files and Folders > Reachy Meetings, then open the app again.");
        return 1;
    }
    if (access(python, X_OK) != 0) {
        alert("Its Python is missing. Build the app again with laptop-app/build_app.sh.");
        return 1;
    }
    static char app_dir[PATH_MAX];
    snprintf(app_dir, sizeof app_dir, "%s/laptop-app", code);
    setenv("REACHY_APP_DIR", app_dir, 1);

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.parse_argv = 0;   // the arguments are the app's (--snapshot ...), not Python's
    PyStatus status = PyConfig_SetBytesString(&config, &config.program_name, python);   // finds that Python's packages
    if (!PyStatus_Exception(status)) status = PyConfig_SetBytesArgv(&config, argc, argv);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        alert("Its Python did not start. Build the app again with laptop-app/build_app.sh.");
        return 1;
    }
    int failed = PyRun_SimpleString(
        "import os, runpy, sys\n"
        "sys.path.insert(0, os.environ['REACHY_APP_DIR'])\n"
        "runpy.run_module('reachy_meetings_app', run_name='__main__', alter_sys=True)\n");
    if (Py_FinalizeEx() < 0) failed = 1;
    return failed ? 1 : 0;
}
