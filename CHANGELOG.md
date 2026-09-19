# Changelog

Notable changes to nimRum. This is a curated summary for users, not an
exhaustive commit log — see the git history for every change.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [2.8.8]

### Fixed
- RX now rejects a non-integer value where an integer is required in the
  config, instead of starting and then crash-looping.

### Changed
- Default log verbosity for RX/TX/source lowered from `warn` to `note`.

## [2.8.7]

- Ships nimRumLib 3.8.0.
