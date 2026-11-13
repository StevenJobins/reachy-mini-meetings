"""The updater with a throwaway key and made-up releases: no network, nothing outside the test folder.   pytest -q"""

import io
import json
import subprocess
import tarfile

import pytest

from reachy_meetings_app import supervisor as sup
from reachy_meetings_app import updates


def code(root, version, marker="ok"):
    """A folder laid out like the app's code, of this version."""
    (root / "laptop-app" / "reachy_meetings_app").mkdir(parents=True)
    (root / "laptop-app" / "reachy_meetings_app" / "__main__.py").write_text(f"print('{marker}')\n")
    (root / "laptop-app" / "VERSION").write_text(version + "\n")
    return root


@pytest.fixture
def key(tmp_path):
    private, public = tmp_path / "key.pem", tmp_path / "key.pub.pem"
    subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(private)], check=True)
    public.write_bytes(subprocess.run(["openssl", "ec", "-in", str(private), "-pubout"], check=True,
                                      capture_output=True).stdout)
    return private, public


def release(tmp_path, private, version, inside=None, name="release"):
    """What release.sh publishes: {file name: bytes}, as a fetch() for it."""
    stage = code(tmp_path / f"stage-{name}-{version}", inside or version)
    package = tmp_path / f"{name}-{version}.tar.gz"
    with tarfile.open(package, "w:gz") as tar:
        tar.add(stage / "laptop-app", "laptop-app")
    signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(private), str(package)], check=True,
                               capture_output=True).stdout
    file = f"reachy-meetings-code-{version}.tar.gz"
    files = {"latest.json": json.dumps({"version": version, "file": file}).encode(), file: package.read_bytes(),
             file + ".sig": signature}
    return files, lambda url: files[url.rsplit("/", 1)[1]]


def test_versions_compare_as_numbers():
    assert updates.newer("0.10.0", "0.9.9") and updates.newer("1.0", "0.99.99") and updates.newer("0.2.1", "0.2")
    assert not updates.newer("0.2.0", "0.2.0") and not updates.newer("0.1.9", "0.2.0")
    assert not updates.newer("", "0.1") and not updates.newer("latest", "0.1") and updates.newer("0.1", "garbage")


def test_choose_runs_a_newer_download_and_else_the_apps_own(tmp_path):
    own, support = code(tmp_path / "own", "0.2.0"), tmp_path / "support"
    assert updates.choose(own, support) == own                       # nothing downloaded
    new = code(support / "code" / "0.3.0", "0.3.0")
    assert updates.choose(own, support) == own                       # downloaded but not named as current
    (support / "code" / "current").write_text("0.3.0\n")
    assert updates.choose(own, support) == new
    assert updates.choose(code(tmp_path / "newer-app", "0.4.0"), support) == tmp_path / "newer-app"   # app reinstalled
    (support / "code" / "current").write_text("../../own\n")
    assert updates.choose(own, support) == own                       # only folders of its own
    (support / "code" / "current").write_text("0.9.0\n")
    assert updates.choose(own, support) == own                       # named but not there


def test_an_update_that_never_reached_its_window_is_not_tried_again(tmp_path):
    own, support = code(tmp_path / "own", "0.2.0"), tmp_path / "support"
    new = code(support / "code" / "0.3.0", "0.3.0")
    (support / "code" / "current").write_text("0.3.0\n")
    updates.mark_starting(new)                                       # the launcher, before running it
    updates.mark_ok(new)                                             # the window came up
    assert updates.choose(own, support) == new
    updates.mark_starting(new)                                       # started again, and this time it died
    assert updates.choose(own, support) == own
    assert (new / ".failed").exists() and updates.choose(own, support) == own


def test_available_only_announces_something_newer(tmp_path, key):
    _, fetch = release(tmp_path, key[0], "0.3.0")
    assert updates.available("0.2.0", "https://x", fetch) == {"version": "0.3.0", "file": "reachy-meetings-code-0.3.0.tar.gz"}
    assert updates.available("0.3.0", "https://x", fetch) is None
    assert updates.available("0.4.0", "https://x", fetch) is None
    assert updates.available("0.2.0", "https://x", lambda url: b'{"version": "0.3.0", "file": "../../evil.tar.gz"}') is None
    assert updates.available("0.2.0", "https://x", lambda url: b'{"version": 3}') is None


def test_install_takes_a_signed_release_and_names_it_current(tmp_path, key):
    support = tmp_path / "support"
    code(support / "code" / "0.2.5", "0.2.5")                        # an earlier download
    _, fetch = release(tmp_path, key[0], "0.3.0")
    folder = updates.install(updates.available("0.2.0", "https://x", fetch), key[1], support, "https://x", fetch)
    assert folder == support / "code" / "0.3.0" and updates.version_of(folder) == "0.3.0"
    assert (support / "code" / "current").read_text().strip() == "0.3.0"
    assert not (support / "code" / "0.2.5").exists()
    assert sorted(p.name for p in (support / "code").iterdir()) == ["0.3.0", "current"]   # nothing left lying around
    assert updates.choose(code(tmp_path / "own", "0.2.0"), support) == folder


def test_a_package_signed_with_another_key_or_changed_is_refused(tmp_path, key):
    support = tmp_path / "support"
    other = tmp_path / "other.pem"
    subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(other)], check=True)
    _, strangers = release(tmp_path, other, "0.3.0", name="strangers")
    with pytest.raises(ValueError, match="not signed"):
        updates.install({"version": "0.3.0", "file": "reachy-meetings-code-0.3.0.tar.gz"}, key[1], support, "https://x", strangers)

    files, _ = release(tmp_path, key[0], "0.3.0")
    file = "reachy-meetings-code-0.3.0.tar.gz"
    files[file] = files[file][:-20] + bytes(20)                      # the package changed on the way
    with pytest.raises(ValueError, match="not signed"):
        updates.install({"version": "0.3.0", "file": file}, key[1], support, "https://x",
                        lambda url: files[url.rsplit("/", 1)[1]])
    assert not (support / "code" / "current").exists() and not (support / "code" / "0.3.0").exists()


def test_an_old_package_served_as_new_is_refused(tmp_path, key):
    files, _ = release(tmp_path, key[0], "0.9.0", inside="0.1.0")    # rightly signed, but it is 0.1.0 inside
    with pytest.raises(ValueError, match="not the version"):
        updates.install({"version": "0.9.0", "file": "reachy-meetings-code-0.9.0.tar.gz"}, key[1], tmp_path / "support",
                        "https://x", lambda url: files[url.rsplit("/", 1)[1]])
    assert not (tmp_path / "support" / "code" / "current").exists()


def test_a_package_cannot_write_outside_its_folder(tmp_path, key):
    package = tmp_path / "evil.tar.gz"
    with tarfile.open(package, "w:gz") as tar:
        info = tarfile.TarInfo("../../escaped.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"evil"))
    signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key[0]), str(package)], check=True,
                               capture_output=True).stdout
    files = {"e.tar.gz": package.read_bytes(), "e.tar.gz.sig": signature}
    with pytest.raises(Exception):
        updates.install({"version": "0.3.0", "file": "e.tar.gz"}, key[1], tmp_path / "support", "https://x",
                        lambda url: files[url.rsplit("/", 1)[1]])
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "support" / "escaped.txt").exists()


def app(tmp_path, key, version="0.2.0", standalone=True):
    repo = code(tmp_path / "app-code", version)
    (repo / "robot" / "scripts").mkdir(parents=True)
    (repo / "robot" / "scripts" / "gateway.py").write_text((sup.REPO / "robot" / "scripts" / "gateway.py").read_text())
    (repo / "laptop-app" / "release_key.pub.pem").write_bytes(key[1].read_bytes())
    s = sup.Supervisor(repo=repo, logs=tmp_path / "logs", settings_path=tmp_path / "app.json", popen=None,
                       environ={"REACHY_STANDALONE": "1", "REACHY_BUNDLE_BIN": "/Apps/Reachy Meetings.app/Contents/Resources/bin"}
                       if standalone else {})
    s.support = tmp_path / "support"
    return s


def test_the_app_fetches_a_newer_release_once_and_says_so(tmp_path, key):
    s = app(tmp_path, key)
    asked = []
    _, fetch = release(tmp_path, key[0], "0.3.0")

    def counting(url):
        asked.append(url.rsplit("/", 1)[1])
        return fetch(url)

    s.check_updates("https://x", counting)
    assert s.update_ready == "0.3.0" and s.status().update == "0.3.0" and s.status().version == "0.2.0"
    assert updates.version_of(s.support / "code" / "0.3.0") == "0.3.0"
    assert "fetched 0.3.0" in (tmp_path / "logs" / "update.log").read_text()
    s.check_updates("https://x", counting)                           # already fetched: not asked again
    assert asked == ["latest.json", "reachy-meetings-code-0.3.0.tar.gz", "reachy-meetings-code-0.3.0.tar.gz.sig"]
    assert str(s.bundle()) == "/Apps/Reachy Meetings.app"


def test_offline_or_a_strangers_release_changes_nothing(tmp_path, key):
    s = app(tmp_path, key)

    def offline(url):
        raise OSError("no network")

    s.check_updates("https://x", offline)
    assert s.update_ready == "" and "no update" in (tmp_path / "logs" / "update.log").read_text()
    other = tmp_path / "other.pem"
    subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(other)], check=True)
    _, strangers = release(tmp_path, other, "0.3.0")
    s.check_updates("https://x", strangers)
    assert s.update_ready == "" and not (s.support / "code" / "current").exists()


def test_a_release_that_failed_here_is_not_fetched_again(tmp_path, key):
    s = app(tmp_path, key)
    failed = code(s.support / "code" / "0.3.0", "0.3.0")
    (failed / ".failed").write_text("did not reach its window\n")
    asked = []
    _, fetch = release(tmp_path, key[0], "0.3.0")
    s.check_updates("https://x", lambda url: asked.append(url) or fetch(url))
    assert s.update_ready == "" and len(asked) == 1 and (failed / ".failed").exists()


def test_a_checkout_does_not_update_itself(tmp_path, key):
    s = app(tmp_path, key, standalone=False)
    s.check_updates("https://x", lambda url: pytest.fail("asked the network"))
    assert s.update_ready == "" and s.bundle() is None
