# Dashboard Implementation Plan (Stuecklistenagent)

## 1. Goal And Scope Of The Dashboard

The new dashboard will be the post-login landing screen for authenticated users. It provides:

- A clear operational overview for BOM review work.
- Quick navigation into the existing Arbeitsflaeche.
- High-signal KPIs for current workload and quality status.
- A searchable recent-files list with progress and status cues.

In scope:

- New `/dashboard` route and full UI implementation.
- Sidebar, top action row, KPI cards, and "Zuletzt geoeffnet" panel.
- Client-side search filter for recent files.
- Navigation wiring to existing Arbeitsflaeche route.
- Responsive behavior for desktop/tablet/mobile.

Out of scope:

- Any redesign of existing Login visuals.
- Any visual/behavioral refactor of Arbeitsflaeche internals.
- Real backend data integration (mock-driven for now).

## 2. Assumptions About Existing Codebase (Found Vs Assumed)

### Found In Repo

- Framework: Next.js App Router with TypeScript.
- Routing: `frontend/src/app/page.tsx` exists as current Arbeitsflaeche route (`/`).
- Login route: `frontend/src/app/login/page.tsx`.
- Auth API helpers: `login`, `getCurrentUser`, `logout` in `frontend/src/lib/api.ts`.
- State/query: React Query via `frontend/src/app/providers.tsx`.
- Styling: Tailwind CSS + CSS variables in `frontend/src/app/globals.css`.
- Brand token already present: `--brand`, `--brand-hover`, `--brand-light`, and `tailwind.config.ts` includes `colors.brand`.

### Assumed For This Implementation

- Existing Arbeitsflaeche route should remain reachable at `/`.
- No existing dashboard-specific backend endpoint exists yet.
- Existing auth cookie/session behavior remains unchanged.
- Existing design tokens are reusable and can be extended without breaking current screens.

## 3. Routing And Navigation Flow

### Required Flow

- Login success redirects to `/dashboard`.
- Dashboard route is protected with the same auth guard pattern (`getCurrentUser`).
- Dashboard button "Neue Datei" navigates to existing Arbeitsflaeche route.
- "Oeffnen ->" per row navigates to Arbeitsflaeche with a file id in query string.

### Detected Arbeitsflaeche Route

- Detected current route: `/` (from `frontend/src/app/page.tsx`).

### Route Constants Strategy

- Create centralized constants in `frontend/src/lib/routes.ts`:
  - `DASHBOARD_ROUTE = "/dashboard"`
  - `WORKSPACE_ROUTE = "/"`

This avoids hardcoded route strings across components.

### Sidebar Routes

- `/dashboard` (Uebersicht)
- `/stuecklisten`
- `/statistik`
- `/einstellungen`

The additional routes will be lightweight stubs to satisfy navigation wiring and preserve future extensibility.

## 4. Component Breakdown (Planned File List + Responsibilities)

### New Files

- `frontend/src/app/dashboard/page.tsx`
  - Dashboard page container with auth guard, search state, and navigation handlers.
- `frontend/src/components/dashboard/sidebar.tsx`
  - Sidebar nav, brand block, active route highlighting, user profile block.
- `frontend/src/components/dashboard/top-header.tsx`
  - Caption, greeting, search input, and primary "Neue Datei" button.
- `frontend/src/components/dashboard/kpi-row.tsx`
  - KPI cards rendering from typed data.
- `frontend/src/components/dashboard/recent-files-panel.tsx`
  - Recent-files list, status badges, progress bars, and "Oeffnen ->" actions.
- `frontend/src/components/dashboard/dashboard-shell.tsx`
  - Shared responsive shell used by dashboard and simple secondary pages.
- `frontend/src/components/dashboard/secondary-page.tsx`
  - Reusable placeholder content for secondary routes.
- `frontend/src/data/mockDashboard.ts`
  - Typed mock data for KPIs and recent files.
- `frontend/src/types/dashboard.ts`
  - Shared status enum and interfaces.
- `frontend/src/lib/routes.ts`
  - Central route constants.
- `frontend/src/app/stuecklisten/page.tsx`
  - Secondary route stub page.
- `frontend/src/app/statistik/page.tsx`
  - Secondary route stub page.
- `frontend/src/app/einstellungen/page.tsx`
  - Secondary route stub page.

### Changed Files

- `frontend/src/app/login/page.tsx`
  - Redirect target only: `/` -> `/dashboard`.

No changes to Arbeitsflaeche internals are planned.

## 5. Data Model / TypeScript Types

### Status Enum

- `type DashboardFileStatus = "Neu" | "In Pruefung" | "Fertig"`

### KPI Type

- `interface DashboardKpi`
  - `id: string`
  - `label: string`
  - `value: string`
  - `tone: "default" | "warning" | "success" | "brand"`

### Recent File Type

- `interface DashboardRecentFile`
  - `id: string`
  - `fileName: string`
  - `description: string`
  - `customer: string`
  - `rows: number`
  - `progressPercent: number`
  - `status: DashboardFileStatus`

### Composite Model

- `interface DashboardMockData`
  - `greetingName: string`
  - `kpis: DashboardKpi[]`
  - `recentFiles: DashboardRecentFile[]`

## 6. Mock Data Strategy

- Keep all mock records in `frontend/src/data/mockDashboard.ts`.
- Export typed constants only (no inline object literals in page components).
- Build realistic rows aligned with requested examples and progress distribution.
- Include at least 5 recent files with mixed statuses.
- Keep IDs stable to support future deep links and API migration.

Migration path to backend:

- Replace mock exports with query hooks or server fetchers.
- Keep UI components unchanged due stable interfaces.

## 7. Design System Mapping (Schaufler Tooling)

### Color Tokens

- Accent token uses centralized brand vars only:
  - `--brand: #004B87`
  - `--brand-hover: #003A6B`
  - `--brand-light: #2A6FB0`
- Background and neutrals:
  - dashboard surface: `#F5F4F1` / `#F7F7F5`
  - cards: white
  - text primary: near-black
  - text secondary and borders from existing neutral vars

Implementation strategy:

- Use `bg-brand`, `hover:bg-brand-hover`, `focus-visible:ring-brand` (Tailwind token mapping).
- Define neutral dashboard surface via one CSS variable in `globals.css` and reference by utility class.
- Avoid repeated literal hex values in components.

### Typography

- Continue current grotesque sans stack via existing global font setup.
- Use large bold greeting and compact uppercase section labels.

### Shape And Elevation

- Cards: `rounded-2xl`, subtle border and soft shadow.
- Primary actions: `rounded-xl`.
- Progress bars and badges use status-specific colors.

## 8. Responsive Behaviour

### Desktop (>= 1024px)

- Two-column shell: fixed sidebar + flexible content pane.
- KPI row in 4 columns.
- Recent-file rows in multi-column horizontal layout.

### Tablet (>= 768px and < 1024px)

- Sidebar collapses to top bar/hamburger drawer.
- KPI row in 2 columns.
- File rows keep core details, wrap secondary actions.

### Mobile (< 768px)

- Drawer-based nav from top bar trigger.
- KPI row stacks to 1 column.
- Each recent file renders as stacked card row:
  - Filename + meta
  - status badge
  - progress
  - open action

## 9. Accessibility Checklist

- Semantic landmarks:
  - `nav` for sidebar navigation.
  - `main` for dashboard content.
- Keyboard navigation:
  - Focusable controls in logical order.
  - Visible focus rings in brand color.
- Active state:
  - `aria-current="page"` on active sidebar item.
- Form/input:
  - Search input has explicit label (visually hidden allowed).
- Status communication:
  - Badges include text labels, not color-only cues.
- Contrast:
  - Ensure text and controls meet AA contrast thresholds.

## 10. Step-By-Step Build Order

1. Create route constants and dashboard types.
2. Add mock dashboard dataset.
3. Build reusable dashboard shell and sidebar components.
4. Build top header with search + primary CTA.
5. Build KPI row component.
6. Build recent-files panel with filtering and open action.
7. Compose all parts in `/dashboard/page.tsx` with auth guard.
8. Add secondary route stubs for sidebar navigation targets.
9. Update login redirects to `/dashboard`.
10. Run lint/type validation and adjust any visual/token conflicts.

## 11. Acceptance Criteria

- `docs/dashboard-plan.md` exists with all required sections.
- `/dashboard` renders full dashboard layout (sidebar, greeting, search, CTA, 4 KPI cards, recent list).
- Schaufler blue is centralized via `brand` token and reused consistently.
- Login success redirects to `/dashboard`.
- "Neue Datei" and every "Oeffnen ->" navigate to Arbeitsflaeche (`/`) with file id query for item links.
- Sidebar items route correctly to their assigned pages.
- Search filters recent files client-side by filename.
- Layout is responsive across desktop/tablet/mobile.
- Accessibility requirements above are implemented.
- Existing Login and Arbeitsflaeche behavior/appearance remain unchanged except routing target updates.