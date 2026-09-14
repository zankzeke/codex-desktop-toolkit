# Supply-chain security and release verification

Codex Bridge Toolkit uses several independent controls for source and Windows release integrity:

- **CodeQL** scans the Python source on pushes to `main`, pull requests targeting `main`, and a weekly schedule.
- **Dependabot** checks Python dependencies and GitHub Actions weekly and opens update pull requests.
- **Locked release dependencies** are stored in `requirements-release.txt` so Windows release builds do not silently drift to a different dependency set.
- **Packaged executable smoke tests** run against both the proxy EXE and GUI EXE before a release is published.
- **SHA256SUMS.txt** is published with every Windows release.
- **GitHub artifact attestations** bind the released EXEs, portable ZIP, and checksum file to the GitHub Actions workflow and source commit that built them. GitHub signs these provenance statements with a short-lived Sigstore-backed certificate.
- **Authenticode** is applied when maintainer signing secrets are configured. The provenance attestation and SHA256 checks still work for unsigned builds.

## Verify a downloaded release with GitHub CLI

Install a current GitHub CLI, download a release artifact, then run:

```powershell
gh attestation verify .\CodexBridgeToolkit-<version>-windows-x64.zip --repo zankzeke/codex-desktop-toolkit
```

The same command can verify the individual executables:

```powershell
gh attestation verify .\CodexBridgeToolkit.exe --repo zankzeke/codex-desktop-toolkit
gh attestation verify .\CodexBridgeProxy.exe --repo zankzeke/codex-desktop-toolkit
```

A successful verification establishes that the file digest is covered by an attestation produced for this repository's GitHub Actions build. It does not mean the software is bug-free; it establishes build provenance.

## Verify SHA256 locally

Download `SHA256SUMS.txt` from the same GitHub Release, then compare hashes with PowerShell:

```powershell
Get-FileHash .\CodexBridgeToolkit.exe -Algorithm SHA256
Get-FileHash .\CodexBridgeProxy.exe -Algorithm SHA256
Get-FileHash .\CodexBridgeToolkit-<version>-windows-x64.zip -Algorithm SHA256
```

The resulting hashes should match the entries in `SHA256SUMS.txt` exactly.

## Maintainer notes

The release workflow needs these GitHub Actions permissions for provenance generation:

```yaml
permissions:
  contents: write
  id-token: write
  attestations: write
  artifact-metadata: write
```

The attestation step runs only after the binaries, portable ZIP, and checksum file have been generated. Release publication happens only after tests, packaged-binary smoke tests, checksums, and provenance generation succeed.
