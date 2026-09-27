# Changelog

## [0.4.2](https://github.com/poolski/homeassistant-outlier-cleaner/compare/v0.4.1...v0.4.2) (2026-09-27)


### Bug Fixes

* **outlier:** batch auto-fix for selected rows into one transaction set ([#16](https://github.com/poolski/homeassistant-outlier-cleaner/issues/16)) ([c4d03f1](https://github.com/poolski/homeassistant-outlier-cleaner/commit/c4d03f1313f10bd1bf5542eb4299d436612c9fcd))

## [0.4.1](https://github.com/poolski/homeassistant-outlier-cleaner/compare/v0.4.0...v0.4.1) (2026-09-27)


### Bug Fixes

* **docs:** update README for date-sorted results and auto-fix suggestions ([#14](https://github.com/poolski/homeassistant-outlier-cleaner/issues/14)) ([8fa571c](https://github.com/poolski/homeassistant-outlier-cleaner/commit/8fa571c4c684d55be409f677602b4717972b8d22))

## [0.4.0](https://github.com/poolski/homeassistant-outlier-cleaner/compare/v0.3.1...v0.4.0) (2026-09-27)


### Features

* **outlier:** suggest an auto-fix value for each detected outlier ([#12](https://github.com/poolski/homeassistant-outlier-cleaner/issues/12)) ([ba0c798](https://github.com/poolski/homeassistant-outlier-cleaner/commit/ba0c79882a9fde9d99ccbcb4e569185ab97115c4))

## [0.3.1](https://github.com/poolski/homeassistant-outlier-cleaner/compare/v0.3.0...v0.3.1) (2026-09-27)


### Bug Fixes

* **outlier:** sort detected outliers by date descending ([#10](https://github.com/poolski/homeassistant-outlier-cleaner/issues/10)) ([2ea5739](https://github.com/poolski/homeassistant-outlier-cleaner/commit/2ea5739bdbdb16dd553453f3e11281e1d7dcccf5))

## [0.3.0](https://github.com/poolski/homeassistant-outlier-cleaner/compare/v0.2.0...v0.3.0) (2026-09-07)


### Features

* use HA's entity picker for statistic selection ([a790322](https://github.com/poolski/homeassistant-outlier-cleaner/commit/a790322547139932a0e6ab481720734c6143749d))


### Bug Fixes

* refresh the date range field after a selection ([fd266dc](https://github.com/poolski/homeassistant-outlier-cleaner/commit/fd266dcb14a75ca706a22aaa1d4ed85bc7dba244))

## [0.2.0](https://github.com/poolski/homeassistant-outlier-cleaner/compare/v0.1.22...v0.2.0) (2026-08-22)


### Features

* use HA's date range picker for the scan range ([d9d658e](https://github.com/poolski/homeassistant-outlier-cleaner/commit/d9d658ef09abaa8fd4a8b32342c530ff337acb94))


### Bug Fixes

* resolve the SQLite path from the recorder's db_url ([22d0b03](https://github.com/poolski/homeassistant-outlier-cleaner/commit/22d0b03ac31bff30c7a0923638671c52b5246592))
* restore the original row values when backups overlap ([c473c87](https://github.com/poolski/homeassistant-outlier-cleaner/commit/c473c8700dad0eb12ab183b96ea7ef3930fd21a4))
