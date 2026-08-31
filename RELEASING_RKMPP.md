# Releasing pyav-rkmpp

The release workflow builds on GitHub's ARM64 runner and publishes from a separate
x64 job. PyPI authentication uses OpenID Connect (Trusted Publishing), so the
repository does not need a long-lived PyPI token.

## One-time GitHub configuration

1. Open `Settings` -> `Environments` in `happyme531/pyav-rkmpp` and create an
   environment named `pypi`.
2. Restrict the `pypi` environment to release tags and, when available, require a
   reviewer.

## One-time PyPI configuration

The `pyav-rkmpp` project does not need to exist before the first upload. Sign in to
PyPI, open the account-level `Publishing` page, and add a pending GitHub publisher:

| Field | Value |
| --- | --- |
| PyPI project name | `pyav-rkmpp` |
| GitHub owner | `happyme531` |
| GitHub repository | `pyav-rkmpp` |
| Workflow filename | `rkmpp.yml` |
| Environment name | `pypi` |

A pending publisher does not reserve the project name. Publish the first release
soon after configuring it. After the first successful upload, PyPI converts it to
a normal Trusted Publisher automatically.

## Publish a release

1. Make sure the ARM64 workflow passes on the release commit.
2. Set `av/about.py` to a new, immutable version such as
   `18.1.0.post2`. PyPI never permits replacing files for an existing version
   and does not accept local version identifiers containing `+`.
3. Create and push the matching tag, including the leading `v`:

   ```bash
   git tag -a 'v18.1.0.post2' -m 'pyav-rkmpp 18.1.0.post2'
   git push origin 'v18.1.0.post2'
   ```

4. Publish a GitHub Release for that tag:

   ```bash
   gh release create 'v18.1.0.post2' --verify-tag --generate-notes
   ```

Publishing the release triggers `.github/workflows/rkmpp.yml`. Its ARM64 job builds
and repairs the wheel; the dependent x64 job downloads that exact artifact and
uploads it to PyPI. The workflow fails before building if the release tag and
`av/about.py` version differ.
