.PHONY: lint lint-fix lint-diff format format-check install-hook uninstall-hook

lint:
	cd pulp_service && ruff check .

lint-fix:
	cd pulp_service && ruff check --fix .

lint-diff:
	bash scripts/ruff-diff-check.sh main

format:
	cd pulp_service && ruff format .

format-check:
	cd pulp_service && ruff format --check --diff .

install-hook:
	@if [ -f .git/hooks/pre-commit ]; then \
		echo "pre-commit hook already exists. Run 'make uninstall-hook' first."; \
		exit 1; \
	fi
	@printf '#!/usr/bin/env bash\nbash scripts/ruff-diff-check.sh HEAD\n' > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "Installed ruff pre-commit hook."

uninstall-hook:
	@rm -f .git/hooks/pre-commit
	@echo "Removed pre-commit hook."
