# Spot Analysis Alpha quick start

This package is `0.1.0-alpha.1` for Windows 11 x64. It is a one-folder portable
client: extract the ZIP to a writable location and run `SpotAnalysis.App.exe`.
No Python, .NET SDK, compiler, administrator rights, or network service is
required on the target machine.

1. Open `examples/alpha-example.png` first to verify the installation.
2. Open a supported 8-bit or 16-bit grayscale PNG.
3. Enter spatial calibration, choose **confirmed**, and select an analysis
   region inside the image. Click **Confirm configuration**.
4. Click **Run real analysis** and wait for the completed state. Review each
   metric's value, unit, validity, and quality reason codes.
5. Use **Export result report** to select an independent output destination.
   Existing reports are never overwritten.
6. Use **Export diagnostics** to save a JSON diagnostic package. The original
   image is excluded by default. Check **Attach original image explicitly** only
   when sharing the image is intended; that option creates a ZIP attachment.

Normal logs are written to `%LOCALAPPDATA%\SpotAnalysis\logs\app.log`.
For the clean-machine acceptance procedure and the complete feedback template, see
`ALPHA-TRIAL-ACCEPTANCE.md`. Feedback should include the client version, input
scenario, steps, expected and observed behavior, severity, package SHA-256, record
identity, and the diagnostic package when available.

The Alpha exercises provisional `standard-profile-v1` and
`quality-profile-v1`; it is not a production release and does not claim formal
physical-accuracy validation.
