# Transparent inter-instance service calls — brainstorming checkpoint

**Status: IN PROGRESS.** This is a snapshot of an ongoing brainstorming session (superpowers:brainstorming,
architectural path), not a reviewed/approved spec. Sections below are either **Decided** or **Open** —
resume by working through the Open items with the user, then produce the final reviewed design doc
(`docs/superpowers/specs/YYYY-MM-DD-transparent-rpc-design.md`) and hand off to writing-plans.

## Goal

A developer depending on a service interface (`ICrud`, or any `YCappuccinoComponent`) should not need to
know whether the implementation is local or lives in another YCappuccino process. The framework should
generate the right proxy (local iPOPO proxy vs RPC proxy to a discovered peer) transparently. Same idea,
later, for a browser frontend calling backend services through auto-generated stubs. Each YCappuccino
framework instance needs a discovery mechanism for the others.

Decomposed into 3 sub-projects (per user's original request):
1. **Interface description model** — this document. Enrich `remote` with a typed, persisted service catalog.
2. **UI shell hosted by `hosts`** — not started. Depends on (1)'s public typed catalog (consumed via `swagger`-style schema) being far enough along.
3. **`permissions_app` UI** (login, user mgmt, permission mgmt) as the first app on top of (1)+(2) — not started. Backend (`authentication.py`, `authorization.py`, `services/login.py`, `services/change_password.py`) already exists in `permissions_app`; this sub-project is UI + catalog wiring.

This document covers sub-project 1 only.

## Audit findings that motivated this design (see conversation for full detail)

- `remote` (rewritten 2026-09-15/16) already has 3 layers: manual named `RemoteCall`, transparent
  `FederatedServiceEndpoint` (service-level fallback local→remote), and generic dynamic proxies
  (`ComponentDirectory` + `remote_proxy.make_generic_proxy` + `dispatch.py`). Discovery today is
  **static registration + HTTP polling** of each peer's `__remote_capabilities__` (no mDNS/broadcast,
  no persistence, no auth). Proxies from layer 3 can only satisfy `Optional[...]`/`list[...]`
  dependencies, never required ones, because they're created asynchronously after peer registration.
- `core.component_factory` treats `YCappuccinoRemote` and `YCappuccinoComponent` identically for DI
  purposes (`_INTERFACE_ROOTS`); there is **no remote-specific logic in `core`** — `remote` bolts on by
  reusing `core.Framework`'s public `instantiate_component`/`list_components` API and iPOPO's ordinary
  service registry.
- `api/proxy.py`'s `Proxy` class (iPOPO integration shim for local components) is **unrelated** to
  `remote`'s proxy generation (`exec()`-synthesized subclasses) — two separate mechanisms.
- `hosts` today is a pure static-file/page server (`HostServlet` + `Host` items). No service-catalog or
  stub-generation concept exists there; the only forward-looking hook is a `cross_origin_isolated` flag
  for a *future* `client` repo that would run Pelix/iPOPO itself under Pyodide (Python-in-browser), not
  JS stub codegen.
- No unified typed service catalog exists anywhere. `swagger`'s `build_openapi` generates full typed
  schemas for CRUD items (via `IItemCatalog.get_schema`) but only `path/method/summary` (no payload
  schema) for `IExposedService` routes, because `IExposedService.call(method, extra_path, params,
  body, subject)` is untyped (`body: Any`) — **every** current implementation (`endpoints_storage.Crud`,
  `permissions_app.LoginService`/`ChangePasswordService`, `scripts.ScriptService`, etc.) is a single
  generic string-dispatched method, not one typed Python method per operation.
- `component-creator` (local-only dynamic component instantiation from a stored `@Item`) is orthogonal
  but relevant prior art: `core.Framework.instantiate_component()` deadlocks if called synchronously
  from within `start()` — must be backgrounded via thread. `remote`'s async proxy creation likely hit
  this already; any new synchronous-at-startup proxy creation (see Open Items) needs to account for it.
- `YCappuccinoRemote` (`api/proxy.py:122`) turns out to be **dead code today**: every interface that
  extends it (`api/hosts.py`'s `IHost`/`IHostFactory`/`IClobReplaceService`, `api/permissions.py`'s
  `ILoginService`/`ITenantTrigger`, `api/scripts.py`'s `IScriptInterpreter`, `api/scheduler.py`'s
  `IScheduler`/`IService`, `api/endpoints.py`'s `IRightManager`/`IEndpoint`/`IHandlerEndpoint`,
  `api/storage.py`'s `IRightSubject`, all of `api/remote.py`) lost its last concrete implementor when
  the corresponding sibling repo was modernized (2026-09-15/16). Confirmed by grep, zero live
  implementors anywhere. Local-vs-remote is being redesigned as a *runtime/deployment* property
  (resolved by discovery/catalog) rather than a *static type* distinction, so there's no remaining role
  for a separate "remote-flavored" base class.

## Decided

1. **Two-tier exposure model.**
   - **Public** (`IExposedService` → `endpoints_service`/`http_server`, unchanged in role): callable by
     any external client (browser, third party). Protected by existing `IAuthorization`/`IAuthentication`.
   - **Internal** (new, in `remote`): component-to-component calls between trusted framework instances.
     Not an `IExposedService` — any `YCappuccinoComponent` another instance needs as a dependency is
     callable. Protected by instance-to-instance trust, not the public user-auth system (today's
     `__remote_capabilities__`/`__remote_dispatch__` are explicitly documented as unauthenticated —
     this closes that gap).
   - Both share the typing/introspection mechanism (below); they differ in which methods are eligible
     and where the routes/dispatch table are mounted.

2. **Typed dispatch, per layer.**
   - **Internal**: introspection is opt-out, not opt-in. Every public method of any
     `YCappuccinoComponent` is eligible, **except** `start`, `stop`, `bind`/`un_bind` (the DI binding
     methods), and (proposed, not yet explicitly confirmed) methods prefixed `_`. No decorator needed —
     access is already gated by instance-to-instance trust (item 4).
   - **Public**: opt-in via a new decorator (name TBD, e.g. `@rpc_method`) on methods of an
     `IExposedService` implementation — only decorated methods become public HTTP routes.
   - Either way, introspection reads real Python type hints (`inspect.signature` +
     `typing.get_type_hints`) at component registration/start time, the same timing
     `component_factory.describe_component()` already uses for DI — building a table
     `{method_name: (callable, {param: type}, return_type)}`.
   - This requires migrating existing generic `call(method, extra_path, params, body, subject)`
     implementations to real typed per-operation methods (**Approach 2**, chosen over keeping the
     generic dispatcher with a side-declared schema, and over a full JSON-RPC 2.0 wire-protocol
     rewrite — see conversation for the 3-approach trade-off). Public HTTP routing becomes one route
     per method (`ServiceRoute` auto-derived from typed methods instead of declared by hand), which
     also gives `swagger` real request/response schemas (closing the gap noted above).

3. **Persisted `ServiceDescriptor` catalog** (new `@Item` model, in `remote`).
   - Fields: service/interface spec name, list of method signatures (name, param types, return type)
     from item 2's introspection, and a reference to the `RemoteServer` (gateway) that exposes it.
   - Stored via the existing `@Item`/`IManager` mechanism → its own collection, not mixed with business
     data (this falls out naturally from how `@Item` persistence already scopes collections per class).
   - **No shared Mongo database across instances.** Each instance stays independent:
     - publishes its own `ServiceDescriptor` entries locally at startup (self-description, from
       introspecting its own loaded components per item 2's internal rules);
     - persists a **local cached copy** of each registered peer's `ServiceDescriptor` entries (replacing
       today's in-memory-only `ComponentDirectory` polling cache), refreshed periodically or on peer
       registration.

4. **Instance-to-instance authentication for the internal channel** — confirmed needed (not just
   "trust the network" as today), replacing the currently-unauthenticated `__remote_capabilities__`/
   `__remote_dispatch__`. **Mechanism not yet designed** (see Open Items).

5. **Cleanup, in scope for this work:**
   - Remove `YCappuccinoRemote` (`api/proxy.py`) and every dead orphaned interface listed in the audit
     section above, including all of `api/remote.py`.
   - Migrate `core/component_factory.py`'s `_INTERFACE_ROOTS` from `(YCappuccinoComponent,
     YCappuccinoRemote)` to `(YCappuccinoComponent,)` only.
   - Update `core/bundles/list_components.py` (`bind(self, a_service: YCappuccinoRemote)`) accordingly.

## Open — resume here

- **Instance-to-instance auth mechanism** (item 4). Candidates not yet discussed with the user in
  detail: shared-secret bearer token stored alongside each `RemoteServer` registration (simplest,
  consistent with existing JWT/token patterns already in `permissions_app`); HMAC-signed requests keyed
  per peer id; something else. Needs its own design pass (issuance, rotation, where the secret is
  configured on both sides).
- **Resolving the required-vs-optional dependency limitation.** Today's async proxy creation means a
  `remote`-satisfied dependency can only be declared `Optional[...]`/`list[...]`, never required. Worth
  exploring whether reading the *local persisted* catalog (item 3) synchronously at
  `describe_component()`/startup time (rather than polling peers over HTTP asynchronously) could let a
  required dependency be resolved immediately when the local cache already knows about a peer — flagged
  as a promising angle in conversation but not yet designed.
- **Decorator name and home** (`@rpc_method` or similar) — which package (`api`? `core`?) defines it.
- **`http_server` routing changes** for one-route-per-method (`ApiServlet` currently routes to a single
  generic per-service endpoint).
- **`swagger` generator changes** to consume the new typed method schemas for public services.
- **Migration plan** for existing `IExposedService`/`ICrud` implementations to typed per-operation
  methods: `endpoints_storage.Crud`, `permissions_app.LoginService`/`ChangePasswordService`,
  `scripts.ScriptService`, and any others found by
  `grep -rln "IExposedService" --include="*.py"` across repos.
- **`_`-prefixed method exclusion** for the internal layer — proposed, not explicitly confirmed by the
  user yet.
- Sub-projects 2 (hosts UI shell) and 3 (permissions_app UI) haven't been brainstormed at all yet.

## Next steps

1. Resume brainstorming: instance-to-instance auth mechanism, then the required-dependency angle.
2. Once sub-project 1's design is complete, run the spec self-review checklist (placeholders,
   consistency, scope, ambiguity), write the final reviewed
   `docs/superpowers/specs/YYYY-MM-DD-transparent-rpc-design.md`, get user sign-off.
3. Invoke `writing-plans` for the implementation plan (this doc is a checkpoint, not that plan).
4. Only after sub-project 1 is at least designed (and ideally the public typed catalog + swagger schema
   land), brainstorm sub-project 2 (hosts UI shell), then sub-project 3 (`permissions_app` UI).
