// Is the user speaking into the open headset mic? (MicSender, since push to talk became a mute button, 2026-11-16.)
// The mic only goes out while this is open: the backend mutes the room captions and the room sound to the headset for
// as long as frames of your voice come in (captions.py ECHO_S), so a mic that sent all the time would mute the room
// for good. Level with hysteresis: opens after AttackS above OpenDb (a click or a knock does not open it), closes
// after HoldS below OpenDb - HysteresisDb (a breath between words does not close it). Not yet measured in the headset:
// OpenDb is a first guess, `adb ... -e micgate <dB>` sets another one, and MicSender logs the levels as [mic].

using System;

public class VoiceGate
{
    public const double DefaultOpenDb = -40;
    const double HysteresisDb = 6, AttackS = 0.06, HoldS = 0.6;

    readonly double openDb;
    double above, below;

    public VoiceGate(double openDb = DefaultOpenDb) { this.openDb = openDb; }

    public bool Open { get; private set; }
    public double OpenDb => openDb;

    /// <summary>One block of the mic: its RMS (linear, 0..1) and its length in seconds.</summary>
    public bool Step(double rms, double seconds)
    {
        double db = Db(rms);
        if (!Open)
        {
            above = db > openDb ? above + seconds : 0;
            if (above >= AttackS) { Open = true; below = 0; }
        }
        else
        {
            below = db < openDb - HysteresisDb ? below + seconds : 0;
            if (below >= HoldS) { Open = false; above = 0; }
        }
        return Open;
    }

    public void Reset()
    {
        Open = false;
        above = below = 0;
    }

    public static double Db(double rms) => 20 * Math.Log10(Math.Max(rms, 1e-6));
}
