
# Self-Documented Makefile approach, borrowed from: https://marmelab.com/blog/2016/02/29/auto-documented-makefile.html

.DEFAULT_GOAL := help

help:
	@grep -E '^[a-zA-Z_-.]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'


setup:  ## Make runtime environment
	@echo "Making runtime environment..."
	@pipenv sync --bare
	-@pipenv check


devsetup:  ## Make runtime environment for development
	@echo "Making runtime environment for development..."
	@pipenv sync --bare --dev
	-@pipenv check


clean: ## Remove bytecode, cache, build and run files
	@echo "Removing bytecode, cache, build and run files..."
	@rm -rf `find . -name __pycache__`
	@rm -f `find . -type f -name '*.py[co]' `
	@rm -rf .cache
	@rm -rf .pytest_cache
	@rm -rf dist
	@rm -rf build
	@rm -rf *.egg-info
	@rm -rf *.log


codestyle: ## Check code style
	@echo "Checking code style..."
	@pycodestyle ribosome --ignore=E501
	@pycodestyle *.py --ignore=E501
