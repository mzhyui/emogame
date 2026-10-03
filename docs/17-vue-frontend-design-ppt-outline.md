# Vue and Frontend Design — 25-Slide PPT Outline

**Audience:** Developers and product teammates who know basic HTML, CSS, and JavaScript.  
**Length:** About 40–45 minutes, including a short demo and questions.  
**Example:** The EmoGame portfolio dashboard in `sakai-vue/`.  
**Format:** Each numbered section is one slide. “On slide” is presentation copy; “Visual” and “Presenter cue” guide production and delivery.

## Slide 01 — Title: Vue and Frontend Design

**On slide**

- From reactive components to a usable data dashboard
- Case study: EmoGame portfolio overview
- Vue 3 · Vite · PrimeVue · FastAPI

**Visual:** Desktop dashboard screenshot with a small mobile preview.  
**Presenter cue:** Introduce the dashboard as a concrete example, not a complete migration of the older Streamlit application.

## Slide 02 — What the audience will learn

**On slide**

- Explain how a Vue view changes when its state changes.
- Trace a dashboard request from filter controls to the API and back.
- Apply hierarchy, responsive layout, accessibility, and data-state design.

**Visual:** Three-part learning path: Vue → data flow → design quality.  
**Presenter cue:** Set expectations: this is an introduction with a real implementation, not a framework API reference.

## Slide 03 — The frontend's job

**On slide**

- HTML gives content and controls meaning.
- CSS organizes the layout and visual language.
- JavaScript connects user actions, application state, and data.
- Good frontend design makes the system's meaning and limits visible.

**Visual:** Layer diagram: semantics, presentation, behavior, data.  
**Presenter cue:** Use one filter field to show all four layers working together.

## Slide 04 — What is Vue?

**On slide**

- Vue is a JavaScript framework for building interfaces from components.
- Reactive state keeps the rendered view in sync with data.
- A full application commonly uses single-file components and the Composition API.

**Visual:** State → Vue component → browser view.  
**Presenter cue:** Vue describes what the view should show for the current state; cite the [Vue introduction](https://vuejs.org/guide/introduction).

## Slide 05 — The component model

**On slide**

- Split an interface into parts with clear responsibilities.
- Compose small controls and cards into a page.
- Keep page layout, data access, and reusable UI roles identifiable.

**Visual:** Dashboard tree: `App.vue` → `AppLayout.vue` → `Dashboard.vue` → PrimeVue controls/charts.  
**Presenter cue:** Point to [`App.vue`](../sakai-vue/src/App.vue) and [`AppLayout.vue`](../sakai-vue/src/layout/AppLayout.vue); the current dashboard page is a large view that could be split further as it grows.

## Slide 06 — Anatomy of a `.vue` file

**On slide**

- `<script setup>` holds imports, state, and behavior.
- `<template>` describes the rendered interface.
- `<style scoped>` contains styles local to the component.

**Visual:** Three labeled excerpts from [`Dashboard.vue`](../sakai-vue/src/views/Dashboard.vue).  
**Presenter cue:** Show a short, real excerpt rather than the full file; see the [Vue single-file component guide](https://vuejs.org/guide/scaling-up/sfc).

## Slide 07 — Templates connect data to UI

**On slide**

- `{{ value }}` displays a value.
- `v-if` and `v-for` control conditional content and lists.
- `v-model` connects a form control to local state.
- `@click` handles an interaction.

**Visual:** Annotated filter and KPI snippets from the dashboard.  
**Presenter cue:** Follow the search field from `v-model="filters.search"` to the Apply button; the [Vue template guide](https://vuejs.org/guide/essentials/template-syntax) covers the syntax.

## Slide 08 — Reactive state: `ref` and `reactive`

**On slide**

- `ref` holds values such as `loading`, `error`, and the API `payload`.
- `reactive` groups editable filter fields.
- Changing tracked state updates the relevant view.

**Visual:** Small state table: variable → UI element it affects.  
**Presenter cue:** Use `payload`, `loading`, and `filters` from [`Dashboard.vue`](../sakai-vue/src/views/Dashboard.vue); see [Vue reactivity fundamentals](https://vuejs.org/guide/essentials/reactivity-fundamentals).

## Slide 09 — Derived state: `computed` and `watch`

**On slide**

- Use `computed` for values derived from existing state.
- Use `watch` when a state change must trigger an effect.
- Keep labels and ranking keys separate: display text may change, data meaning must not.

**Visual:** `rankingMetric` → `rankingRows` → table; theme selection → chart palette.  
**Presenter cue:** The dashboard computes cards and chart data, while a watcher responds to theme changes. Reference the [computed guide](https://vuejs.org/guide/essentials/computed) and [watcher guide](https://vuejs.org/guide/essentials/watchers).

## Slide 10 — Component communication

**On slide**

- Pass data downward with props.
- Report user actions upward with emitted events.
- Use shared state only when several distant components truly need it.

**Visual:** Parent card with arrows to and from a reusable filter control.  
**Presenter cue:** This is a design pattern for future dashboard extraction, not a claim that the current page already uses custom prop/event wiring; see the [Vue component guide](https://vuejs.org/guide/essentials/component-basics).

## Slide 11 — Lifecycle and asynchronous work

**On slide**

- Load dashboard data when the page mounts.
- Show progress while the request is running.
- Abort obsolete work and release listeners when the page unmounts.

**Visual:** Mount → request → render → unmount timeline.  
**Presenter cue:** Trace `onMounted`, `loadDashboard`, `AbortController`, and `onBeforeUnmount` in [`Dashboard.vue`](../sakai-vue/src/views/Dashboard.vue).

## Slide 12 — Routes and application shell

**On slide**

- Vue Router maps a URL to a page component.
- The layout supplies navigation, top bar, footer, and a content slot.
- EmoGame currently routes `/` to the portfolio dashboard.

**Visual:** URL `/` → `AppLayout` → `Dashboard`; add a separate 404 arrow only as a possible future design.  
**Presenter cue:** The current wildcard route redirects to `/`; do not imply separate pages exist. See [`router/index.js`](../sakai-vue/src/router/index.js) and the [Vue Router guide](https://router.vuejs.org/guide/).

## Slide 13 — The development toolchain

**On slide**

- Vite serves the Vue app during development and builds production assets.
- `npm run dev` starts the local frontend; `npm run build` checks the bundle.
- The local Vite proxy forwards `/api` to FastAPI on port 8000.

**Visual:** Browser → Vite `:5173` → FastAPI `:8000`.  
**Presenter cue:** Show [`vite.config.mjs`](../sakai-vue/vite.config.mjs); distinguish development proxying from production routing. See the [Vite guide](https://vite.dev/guide/).

## Slide 14 — UI library and visual foundation

**On slide**

- PrimeVue supplies controls such as `Select`, `DatePicker`, `DataTable`, and `Chart`.
- Sakai supplies the navigation shell and layout patterns.
- Theme tokens support consistent color and dark mode.

**Visual:** Component inventory with one real dashboard screenshot.  
**Presenter cue:** Show [`main.js`](../sakai-vue/src/main.js) for PrimeVue and the Aura theme; a component library still needs deliberate content and interaction design.

## Slide 15 — EmoGame frontend architecture

**On slide**

- `Dashboard.vue` owns filters, request state, and presentation.
- `DashboardService.js` serializes filters and calls the read-only API.
- FastAPI reuses the existing portfolio query layer.

**Visual:** Component → service → `/api/dashboard/overview` → `dashboard.query`/SQLite → response.  
**Presenter cue:** Trace the path through [`DashboardService.js`](../sakai-vue/src/service/DashboardService.js) and [`api/routes/dashboard.py`](../api/routes/dashboard.py).

## Slide 16 — Design the data contract first

**On slide**

- Filters, KPIs, rankings, charts, timeline, and evidence status arrive together.
- The API rejects reversed date ranges with HTTP 422.
- A missing numeric value stays `null`; zero and negative values remain valid values.

**Visual:** Simplified response object with `scope`, `kpis`, `rankings`, and `summary`.  
**Presenter cue:** Explain why `null` becomes an em dash in the UI instead of an invented zero. Use [`api/routes/dashboard.py`](../api/routes/dashboard.py) as the contract example.

## Slide 17 — Information architecture of the page

**On slide**

- Start with the analysis scope and shared filters.
- Move from summary KPIs to comparisons and rankings.
- End with coverage gaps and evidence status.

**Visual:** Vertical page map: header → filters → KPIs → charts → rankings → timeline → coverage/evidence.  
**Presenter cue:** Let users answer “What am I looking at?” before “Which item is highest?”

## Slide 18 — Visual hierarchy and interaction

**On slide**

- Make the page title, scope, and primary metrics easy to scan.
- Group related filters; provide Apply and Reset actions.
- Use text labels alongside color and icons.

**Visual:** Dashboard crop with numbered attention order.  
**Presenter cue:** Use a single filtered-view task to test whether the hierarchy supports a quick answer.

## Slide 19 — A practical design system

**On slide**

- Define reusable color, type, spacing, border, and surface decisions.
- Connect chart colors to the active theme.
- Keep data-status language consistent across cards, tables, and messages.

**Visual:** Small token board with light and dark surfaces.  
**Presenter cue:** The dashboard reads PrimeVue CSS variables for charts and responds to theme changes; show the relevant code in [`Dashboard.vue`](../sakai-vue/src/views/Dashboard.vue).

## Slide 20 — Responsive layout

**On slide**

- Reflow multi-column content into readable narrow-screen sections.
- Keep controls usable and preserve the content order.
- Test charts and tables at desktop and phone widths.

**Visual:** Same dashboard section at wide and narrow widths.  
**Presenter cue:** The current page combines a flexible grid with CSS breakpoints; connect this to [MDN's responsive design guide](https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/CSS_layout/Responsive_Design).

## Slide 21 — Design honest data visualizations

**On slide**

- Match the chart to the question: distribution, relationship, or time.
- Show units, denominators, and coverage beside the result.
- Keep game-wide revenue context distinct from filtered skin release events.

**Visual:** Three small examples: quality bars, score × cash scatter, release/revenue timeline.  
**Presenter cue:** Explain the different populations represented by the timeline layers and the scatter's dual-value coverage.

## Slide 22 — Loading, empty, and error states

**On slide**

- Loading: show skeletons in the shape of incoming content.
- Empty: explain what the current filter excluded.
- Error: show what failed and offer a retry action.

**Visual:** Three-state strip using the current dashboard's skeleton, empty chart, and error message.  
**Presenter cue:** A useful page still communicates clearly when its data cannot be shown.

## Slide 23 — Accessible interaction

**On slide**

- Give form controls visible labels and icon buttons accessible names.
- Make keyboard focus and reading order clear.
- Check contrast and provide text alternatives for chart conclusions.

**Visual:** Annotated search label, top-bar button, and chart summary.  
**Presenter cue:** Show the dashboard's `label`/`id` pairs and `aria-label` buttons. Treat chart text alternatives as a review item, not a verified property of the current implementation; see [W3C form labels](https://www.w3.org/WAI/tutorials/forms/labels/) and [WCAG 2.2](https://www.w3.org/TR/wcag/).

## Slide 24 — Validate the experience

**On slide**

- Build the frontend and test the API contract.
- Walk a filter → KPI/chart → ranking journey at two viewport sizes.
- Check missing values, date errors, theme changes, and keyboard use.

**Visual:** Compact validation matrix: behavior × desktop/mobile × expected result.  
**Presenter cue:** Local commands: `cd sakai-vue && npm run build`; from the repository root, `.venv/bin/python -m unittest tests.test_api`. A passing build does not certify usability.

## Slide 25 — Recap and next steps

**On slide**

- Vue turns state and components into a reactive interface.
- A strong dashboard connects design decisions to a trustworthy data contract.
- Next: run the local demo, review one user journey, then improve one friction point.

**Visual:** Return to the architecture diagram with “Vue,” “data,” and “design” highlighted.  
**Presenter cue:** Close with a live filter change and one question from the audience.

---

## Production notes (not slides)

- **Demo setup:** Start FastAPI with `.venv/bin/uvicorn api.main:app --reload --port 8000`, then run `cd sakai-vue && npm ci && npm run dev` in a second terminal. The frontend is served by Vite; the backend data are local and read-only.
- **Screenshot plan:** Capture the full dashboard for slides 1 and 17, a filter/detail crop for slides 7 and 18, light/dark comparisons for slide 19, and desktop/mobile comparisons for slide 20. Remove or mask local identifiers if a screenshot is shared externally.
- **Scope:** This case study covers the Vue portfolio overview. The older Streamlit application contains other pages that are outside this deck's implementation walkthrough.
- **Sources:** [Vue guide](https://vuejs.org/guide/introduction), [Vue Router guide](https://router.vuejs.org/guide/), [Vite guide](https://vite.dev/guide/), [MDN responsive design](https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/CSS_layout/Responsive_Design), and [W3C accessibility guidance](https://www.w3.org/WAI/tutorials/forms/labels/). Project-specific facts come from the linked repository files above.
