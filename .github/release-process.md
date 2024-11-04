# Release Process

This document describes how we handle releases and deployments for both our self-hosted (main) and cloud versions.

## Branch Structure

- `main`: The primary branch for self-hosted deployments
- `cloud`: The branch for our hosted cloud version

## Automated Release Process

### For Self-Hosted Version (main)

1. **Pull Request Stage**
   - All PRs to `main` trigger automated tests
   - Tests include:
     - Python type checking (mypy)
     - Unit tests (pytest)
     - Code coverage reporting
   - All checks must pass before merge

2. **Release Stage**
   - Upon successful merge to `main`:
     - A new release is automatically created
     - Version number is automatically incremented following semantic versioning:
       - PATCH (x.x.X): Bug fixes and minor changes
       - MINOR (x.X.x): New features, backward compatible
       - MAJOR (X.x.x): Breaking changes
     - Release notes are automatically generated from PR descriptions
     - Release artifacts are created and published

### For Cloud Version

1. **Sync Stage**
   - After each release on `main`:
     - Changes are automatically merged into the `cloud` branch
     - Cloud-specific tests are run

2. **Deployment Stage**
   - Upon successful tests on `cloud` branch:
     - Automatic deployment to production infrastructure
     - Health checks are performed
     - Rollback procedures are in place if needed

## Version Numbering

We follow [Semantic Versioning](https://semver.org/):
- Format: MAJOR.MINOR.PATCH
- Example: 1.2.3

## Release Notes

Release notes are automatically generated and include:
- New features
- Bug fixes
- Breaking changes
- Migration instructions (if applicable)
- Contributors


## Manual Intervention

While the process is automated, maintainers can:
- Force version bumps
- Edit release notes
- Trigger manual deployments
- Halt automatic deployments

## Monitoring Releases

- Release status can be monitored in GitHub Actions
- Deployment status is available in our monitoring dashboard
- Release notifications are sent to our communication channels

---

For questions about the release process, please open an issue or contact the maintainer: @Jonathan-Adly or @Abdullah13521 @HalemoGPA
