# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

Langflow is a visual workflow builder for AI-powered agents. It has a Python/FastAPI backend, React/TypeScript frontend, and a lightweight executor CLI (lfx).

## This fork — Agent Marketplace

This repo is a fork. `main` tracks upstream; all marketplace work lands on
**`v1.9.1-market`**, which is the base for feature branches and PRs here. The
platform that surrounds this builder lives in the sibling repo
`0to1-agents-market` — read its `CLAUDE.md` first.

**Keep docs in sync with the code.** Behaviour described in a doc must be
updated in the same commit, here or in the sibling repo. Write docs short and
precise: simple words, easy to understand.

### Auth (differs from upstream)

- The builder is **Keycloak SSO only**. Upstream's username/password form and
  the `/signup` page are removed; `/login` offers **Sign in as producer**
  (`/api/v1/login/oidc/authorize`), **Sign in as consumer** and **Sign Up**.
- The last two go through `GET /api/v1/login/oidc/marketplace?path=login|signup`,
  which redirects to `AGENTS_MARKET_FRONTEND_URL`. `path` is allowlisted, so the
  route cannot be used as an open redirect. **Signup lives in the marketplace**,
  never here.
- Builder access is decided by `oidc_sso.builder_profiles()`: the realm roles
  `agent_producer` / `platform_developer` / `platform_admin` grant the personal
  profile, and admin/producer in an `/orgs/<id>` group grants that org's
  profile. Anyone with no profile at all is signed back out of Keycloak and
  returned to `/login` with an `sso_error` cookie.
- **Organization profiles.** A user acts personally or inside one org, and each
  profile is its own Langflow `User` row (`alice@x.com#acme`). Flows, folders
  and variables are already scoped by `user_id`, so this isolates profiles with
  no query changes, and each profile gets its own `LITELLM_KEY`. Switching
  re-enters `/api/v1/login/oidc/authorize?org_id=`, so membership is re-checked
  against a fresh token rather than trusted from the browser. Use
  `split_scoped_username()` before sending a username to another service —
  the marketplace keys its `users` table on the plain email.
  See `0to1-agents-market/docs/organizations-and-roles.md`.
- **Logout must hit the backend.** `refresh_token_lf` is HttpOnly, so clearing
  cookies in the browser leaves it in place and the next `/refresh` silently
  mints a new access token for the user who just logged out. SSO sessions then
  also go to `/api/v1/login/oidc/logout` to end the Keycloak session.
- Deleting an auth cookie requires the **same attributes it was set with**
  (`httponly`, `samesite`, `secure`, `domain`), or the browser keeps it.
- `sso_provider` / `kc_id_token` live for the refresh-token lifetime, not the
  access-token lifetime: logout needs them to detect an SSO session and to pass
  `id_token_hint`.
- `sso_profiles` is readable by the frontend (the org switcher renders from it)
  and is **display only**. A cookie value containing base64 padding gets quoted
  by the server, so strip surrounding quotes when reading it.

### Local (k3s) development

`docker/local_backend.Dockerfile` is the dev image: source is mounted at `/app`,
and the uv venv + cache live at `/uv` (a PVC) — not in the mounted source, which
cannot do uv's atomic renames, and not in `/tmp`, which is wiped on restart.
Cluster setup, ports and troubleshooting are in
`0to1-agents-market/deployment/local/LOCAL_DEV.md`.

### Sibling-repo docs

| Topic | Doc |
|---|---|
| Signup, roles, `POST /api/auth/register` | `0to1-agents-market/docs/user-registration.md` |
| Organizations, roles, profile isolation | `0to1-agents-market/docs/organizations-and-roles.md` |
| Keycloak realm import, client secrets, reset script | `0to1-agents-market/docs/keycloak-realm.md` |
| Verification email (SES) | `0to1-agents-market/docs/ses-email.md` |
| Token → LiteLLM key chain (why the `basic` scope matters) | `0to1-agents-market/docs/litellm-key-resolution.md` |
| Publish → deploy pipeline | `0to1-agents-market/docs/publish-deploy-ux.md` |

## Prerequisites

- **Python:** 3.10-3.13
- **uv:** >=0.4 (Python package manager)
- **Node.js:** >=20.19.0 (v22.12 LTS recommended)
- **npm:** v10.9+
- **make:** For build coordination

## Common Commands

### Development Setup
```bash
make init              # Install all dependencies + pre-commit hooks
make run_cli           # Build and run Langflow (http://localhost:7860)
make run_clic          # Clean build and run (use when frontend issues occur)
```

### Development Mode (Hot Reload)
```bash
make backend           # FastAPI on port 7860 (terminal 1)
make frontend          # Vite dev server on port 3000 (terminal 2)
```

For component development, enable dynamic loading:
```bash
LFX_DEV=1 make backend                    # Load all components dynamically
LFX_DEV=mistral,openai make backend       # Load only specific modules
```

### Code Quality
```bash
make format_backend    # Format Python (ruff) - run FIRST before lint
make format_frontend   # Format TypeScript (biome)
make format            # Both
make lint              # mypy type checking
```

### Testing
```bash
make unit_tests                    # Backend unit tests (pytest, parallel)
make unit_tests async=false        # Sequential tests
uv run pytest path/to/test.py      # Single test file
uv run pytest path/to/test.py::test_name  # Single test

make test_frontend                 # Jest unit tests
make tests_frontend                # Playwright e2e tests
```

### Database Migrations
```bash
make alembic-revision message="Description"  # Create migration
make alembic-upgrade                         # Apply migrations
make alembic-downgrade                       # Rollback one version
```

## Architecture

### Monorepo Structure
```
src/
├── backend/
│   ├── base/langflow/     # Core backend package (langflow-base)
│   │   ├── api/           # FastAPI routes (v1/, v2/)
│   │   ├── components/    # Built-in Langflow components
│   │   ├── services/      # Service layer (auth, database, cache, etc.)
│   │   ├── graph/         # Flow graph execution engine
│   │   └── custom/        # Custom component framework
│   └── tests/             # Backend tests
├── frontend/              # React/TypeScript UI
│   └── src/
│       ├── components/    # UI components
│       ├── stores/        # Zustand state management
│       └── icons/         # Component icons
└── lfx/                   # Lightweight executor CLI
```

### Key Packages
- **langflow**: Main package with all integrations
- **langflow-base**: Core framework (api, services, graph engine)
- **lfx**: Standalone CLI for running flows (`lfx serve`, `lfx run`)

### Service Layer
Backend services in `src/backend/base/langflow/services/`:
- `auth/` - Authentication
- `database/` - SQLAlchemy models and migrations
- `cache/` - Caching layer
- `storage/` - File storage
- `tracing/` - Observability integrations

## Component Development

Components live in `src/backend/base/langflow/components/`. To add a new component:

1. Create component class inheriting from `Component`
2. Define `display_name`, `description`, `icon`, `inputs`, `outputs`
3. Add to `__init__.py` (alphabetical order)
4. Run with `LFX_DEV=1 make backend` for hot reload

**IMPORTANT:** Changing a component's class name is a breaking change and should never be done. The class name serves as an identifier used to match components in saved flows and to flag them for updates in the UI. Renaming it will break existing flows that use that component.

### Component Structure
```python
from langflow.custom import Component
from langflow.io import MessageTextInput, Output

class MyComponent(Component):
    display_name = "My Component"
    description = "What it does"
    icon = "component-icon"  # Lucide icon name or custom

    inputs = [
        MessageTextInput(name="input_value", display_name="Input"),
    ]
    outputs = [
        Output(display_name="Output", name="output", method="process"),
    ]

    def process(self) -> Message:
        # Component logic
        return Message(text=self.input_value)
```

### Component Testing
Tests go in `src/backend/tests/unit/components/`. Use base classes:
- `ComponentTestBaseWithClient` - Components needing API access
- `ComponentTestBaseWithoutClient` - Pure logic components

Required fixtures: `component_class`, `default_kwargs`, `file_names_mapping`

## Frontend Development

- **React 19** + TypeScript + Vite
- **Zustand** for state management
- **@xyflow/react** for graph visualization
- **Tailwind CSS** for styling

### Custom Icons
1. Create SVG component in `src/frontend/src/icons/YourIcon/`
2. Export with `forwardRef` and `isDark` prop support
3. Add to `lazyIconImports.ts`
4. Set `icon = "YourIcon"` in Python component

## Testing Notes

- `@pytest.mark.api_key_required` - Tests requiring external API keys
- `@pytest.mark.no_blockbuster` - Skip blockbuster plugin
- Database tests may fail in batch but pass individually
- Pre-commit hooks require `uv run git commit`
- Always use `uv run` when running Python commands

### Graph Testing Pattern

Proper Graph tests follow this pattern:
1. Build graph with connected components
2. Connect them via `.set()` calls
3. Call `async_start` and iterate over the results
4. Validate the results

### Testing Best Practices

- Avoid mocking in tests when possible
- Prefer real integrations for more reliable tests

## Version Management
```bash
make patch v=1.5.0  # Update version across all packages
```

This updates: `pyproject.toml`, `src/backend/base/pyproject.toml`, `src/frontend/package.json`

## Pre-commit Workflow

1. Run `make format_backend` (FIRST - saves time on lint fixes)
2. Run `make format_frontend`
3. Run `make lint`
4. Run `make unit_tests`
5. Commit changes (use `uv run git commit` if pre-commit hooks are enabled)

## Pull Request Guidelines

- Follow [semantic commit conventions](https://www.conventionalcommits.org/)
- Reference any issues fixed (e.g., `Fixes #1234`)
- Ensure all tests pass before submitting

## Documentation

Documentation uses Docusaurus and lives in `docs/`:
```bash
cd docs
yarn install
yarn start        # Dev server on port 3000 (prompts for 3001 if 3000 is in use)
```
