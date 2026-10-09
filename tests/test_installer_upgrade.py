"""Execute installer policy callbacks in a harness that aborts before installation.

No application is installed, no real uninstall registration is written, and all
payloads are disposable text fixtures. This does not replace native upgrade QA.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


def application_control_blocked(folder: Path) -> bool:
    """Correlate a loader failure with a policy event for this unique harness."""
    environment = os.environ.copy()
    environment.pop("PSModulePath", None)
    environment["SUITE_HARNESS_NAME"] = folder.name
    shell = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    check = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command",
         "$events = @(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; Id=3077; StartTime=(Get-Date).AddMinutes(-2)} -ErrorAction SilentlyContinue | Where-Object { $_.Message.Contains($env:SUITE_HARNESS_NAME) }); if ($events.Count -gt 0) { exit 42 } else { exit 0 }"],
        env=environment, capture_output=True, timeout=20,
    )
    return check.returncode == 42


@unittest.skipUnless(os.name == "nt", "Inno Setup harness requires Windows")
class InstallerUpgradeTests(unittest.TestCase):
    def test_actual_pascal_version_lock_and_success_cleanup_callbacks(self):
        compiler = shutil.which("ISCC.exe")
        if not compiler:
            self.skipTest("Inno Setup compiler is unavailable")
        source = (ROOT / "installer/setup.iss").read_text(encoding="utf-8")
        self.assertNotIn('Type: filesandordirs; Name: "{app}\\_internal"', source)
        policy = source.split("[Code]\n", 1)[1]
        with tempfile.TemporaryDirectory(prefix="DataRefinery-installer-policy-") as temporary:
            folder = Path(temporary)
            app = folder / "App04_DataRefinery_v2.0.1.exe"
            app.write_text("new-payload-fixture", encoding="ascii")
            old = folder / "App04_DataRefinery_v2.0.0.exe"
            old.write_text("old-payload-fixture", encoding="ascii")
            changed = folder / "App04_DataRefinery_v1.9.0.exe"
            changed.write_text("changed-payload-fixture", encoding="ascii")
            settings = folder / "UserSetting"
            settings.mkdir()
            (settings / "settings.json").write_text('{"preserve": true}', encoding="utf-8")
            result = folder / "result.txt"
            harness = folder / "harness.iss"
            harness.write_text(
                f'''#define AppVersion "2.0.1"
#define AppExeName "{app.name}"
#define PreviousAppId "Phase2-No-Previous-Registration"
#define ExpectedAppHash GetSHA256OfFile("{app}")
[Setup]
AppId=Phase2-DataRefinery-Verification
AppName=Installer policy verification
AppVersion=2.0.1
DefaultDirName={folder}
OutputDir={folder}
OutputBaseFilename=harness
PrivilegesRequired=lowest
Uninstallable=no
CreateAppDir=yes
DisableStartupPrompt=yes
[CustomMessages]
UpgradeBlocked=Blocked by installer policy
[Code]
{policy}
procedure Require(Condition: Boolean; Detail: String);
begin
  if not Condition then RaiseException(Detail);
end;

procedure InitializeWizard;
var
  Restart: Boolean;
  Probe: TFileStream;
  Blocked: Boolean;
begin
  Require(IsOlderAppExecutable('App04_DataRefinery_v2.0.0.exe'), 'older patch');
  Require(IsOlderAppExecutable('App04_DataRefinery_v1.99.99.exe'), 'older major');
  Require(not IsOlderAppExecutable('App04_DataRefinery_v2.0.1.exe'), 'current version');
  Require(not IsOlderAppExecutable('App04_DataRefinery_v3.0.0.exe'), 'newer version');
  Require(not IsOlderAppExecutable('App04_DataRefinery_v2.00.0.exe'), 'noncanonical');
  Require(not IsOlderAppExecutable('App04_DataRefinery_v2.0.-1.exe'), 'negative');
  Require(not IsOlderAppExecutable('App04_DataRefinery_v2.0.0.extra.exe'), 'suffix');
  Require(not IsOlderAppExecutable('other.exe'), 'unrelated');
  Restart := False;
  Require(PrepareToInstall(Restart) = '', 'prepare');
  Require(not PreviousInstallOwned, 'unregistered install ownership');
  OlderNames.Add('{old.name}');
  OlderHashes.Add(GetSHA256OfFile('{old}'));
  OlderNames.Add('{changed.name}');
  OlderHashes.Add('different-before-install-hash');
  CurStepChanged(ssDone);
  Require(FileExists('{old}'), 'no cleanup without ownership');
  PreviousInstallOwned := True;
  SaveStringToFile('{app}', 'tampered', False);
  CurStepChanged(ssDone);
  Require(FileExists('{old}'), 'no cleanup when replacement hash differs');
  SaveStringToFile('{app}', 'new-payload-fixture', False);
  CurStepChanged(ssInstall);
  Require(FileExists('{old}'), 'no premature cleanup');
  Probe := TFileStream.Create('{app}', fmOpenRead or fmShareDenyNone);
  try
    Blocked := False;
    try CheckExecutableReplaceable('{app}'); except Blocked := True; end;
    Require(Blocked, 'locked payload must block');
  finally
    Probe.Free;
  end;
  CheckExecutableReplaceable('{app}');
  CurStepChanged(ssDone);
  Require(not FileExists('{old}'), 'verified old leaf cleanup');
  Require(FileExists('{changed}'), 'changed old file preserved');
  Require(FileExists('{app}'), 'new payload preserved');
  Require(FileExists('{settings / 'settings.json'}'), 'UserSetting preserved');
  SaveStringToFile('{result}', 'PASS', False);
  RaiseException('Verification completed before installation.');
end;
''', encoding="utf-8",
            )
            compiled = subprocess.run([compiler, "/Q", str(harness)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            try:
                ran = subprocess.run(
                    [str(folder / "harness.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={folder}", f"/LOG={folder / 'harness.log'}"],
                    capture_output=True, text=True, timeout=60,
                )
            except OSError as error:
                if error.winerror == 4551:
                    self.skipTest("Harness compiled; Windows Application Control blocked unsigned execution (4551)")
                raise
            if not result.exists() and ran.returncode:
                for _ in range(3):
                    # Event delivery can lag the loader's exit on Windows.
                    time.sleep(1)
                    if application_control_blocked(folder):
                        self.skipTest("Harness compiled; Code Integrity event 3077 confirms unsigned inner loader was blocked")
            self.assertTrue(result.exists(), f"Harness failed before result: {ran.returncode}\n{(folder / 'harness.log').read_text(errors='replace') if (folder / 'harness.log').exists() else ran.stderr}")
            self.assertEqual(result.read_text(), "PASS")
            self.assertFalse((folder / "unins000.exe").exists())
            self.assertEqual((settings / "settings.json").read_text(), '{"preserve": true}')


if __name__ == "__main__":
    unittest.main()
