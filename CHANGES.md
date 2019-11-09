
# Ribosome release history

## Next

* New: #11 Support AWS credentials profiles


## 0.6.5 / 2019-11-05

* Fix: Allow empty sudo password to be entered by default


## 0.6.4 / 2019-09-23

* Fix: #9 Deploy command checks for loaded services
* Fix: Logging timestamps made fixed length millisecond precision


## 0.6.3 / 2019-06-06

* Fix: Load service functionality was broken
* Fix: Logging timestamps made ISO 8601
* Fix: Small changes in messaging and reporting


## 0.6.2 / 2019-05-17

* Fix: Deprecation warnings from third-party libs suppressed


## 0.6.1 / 2019-05-14

* Fix: Unload service functionality was broken


## 0.6.0 / 2019-05-10

* Refactoring: Error handling based on exceptions
* Refactoring: Code slightly reorganized


## 0.5.0 / 2018-08-29

* New: JSON meta descriptor format support
* Fix: User messaging slightly improved


## 0.4.0 / 2018-08-15

* New: Slack reporting support
* Fix: Release archives now removed on garbage collect
* Fix: Messaging to user slightly improved


## 0.3.3 / 2018-08-10

* Fix: service index reading was broken with new version of ruamel.yaml


## 0.3.2 / 2018-08-10

* Fix: codons reading was broken with new version of ruamel.yaml


## 0.3.1 / 2018-08-10

* Fix: cleanup command in codons template


## 0.3.0 / 2018-08-10

* #2: Commands [gc] and [ls] added
* Show command output now stable - versions are sorted
* Small improvements and refactoring


## 0.2.0 / 2018-07-27

Incompatible changes:
* No [changes] parameter is returned with scminfo and placed inside meta.py file

Fixed:
* #1: Clean version incorrectly determined


## 0.1.0 / 2018-07-17

Hooray, this is the first version.
