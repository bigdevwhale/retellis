# MockAdapter Removal - Summary

## ✅ Completed (Production Code)

### Phase 1: Backend Core Changes
- ✅ **Deleted**: `apps/api/src/ai_companion_api/llm/mock_adapter.py`
- ✅ **Updated**: `provider.py` - removed mock from build_chain(), DEFAULT_MODELS, docstring
- ✅ **Added**: `NoProviderAvailableError` exception class
- ✅ **Updated**: `fallback.py` - removed mock re-raise branch
- ✅ **Updated**: `router.py` - removed mock from display_chain(), _kind_status()
- ✅ **Updated**: `routers/llm.py` - budget hard-stop now returns HTTP 402 instead of mock
- ✅ **Updated**: `turn/__init__.py` - messenger path (empty chain instead of mock-only)
- ✅ **Updated**: Post-turn checks in both routers/llm.py and turn/__init__.py

### Phase 2: Contract Changes
- ✅ **TypeScript** (`packages/contracts/src/ts/index.ts`): Tightened 5 fields from `z.string()` to `ProviderKind`:
  - Usage.provider_kind (line 207)
  - RoutingNode.kind (line 218)
  - ProviderSummary.kind (line 228)
  - FallbackEvent.from_kind/to_kind (lines 306-307)
  - UsageEvent.provider_kind (line 313)
- ✅ **Python** (`packages/contracts/src/py/ai_companion_contracts/models.py`): Mirrored all 5 changes
- ✅ **Verified**: `pnpm contracts:check` - **46 models in parity**

### Phase 3: Frontend Cleanup
- ✅ **apps/web/lib/api-client.ts**: Updated type comments (lines 81, 376)
- ✅ **apps/web/components/screens/RoutingScreen.tsx**: Removed mock case from chainNodeLabel()
- ✅ **apps/web/components/screens/ChatScreen.tsx**: Removed mock from KIND_LABEL
- ✅ **apps/web/lib/i18n.tsx**: Removed `rt.chain.local` key (line 1107)
- ✅ **apps/web/components/GuestShowcase.tsx**: Removed mock chips and updated comments
- ✅ **Verified**: `pnpm typecheck` - **compilation successful**

## 🔄 Remaining (Test Rewrites - Phase 4)

### Test Categories Affected

**Category A: Chain-Ordering Tests** (`apps/api/tests/test_routing.py`)
- `test_build_chain_no_keys_is_just_mock` → Now expects empty chain + NoProviderAvailableError
- `test_build_chain_orders_byok_then_env_then_mock` → Remove mock from expected chain
- `test_display_chain_zero_config_is_ollama_standby_then_mock` → Update expected chain display
- `test_get_routing_returns_state_with_summary` → Mock no longer appears in summary

**Category B: Fallback-Behavior Tests**
Replace MockAdapter candidates with local `_OkAdapter` doubles:
- `test_llm_stream.py::test_fallback_to_mock_on_provider_failure`
- `test_routing.py::test_run_with_fallback_falls_over_to_mock`
- `test_sprint3_scope.py::test_i01_fallback_drops_partial_before_persist`
- `test_safety_screen.py` (outbound test)
- `test_hosted_credits_gate.py`

**Category C: E2E Mock-Path Tests**
Inject fake adapter via monkeypatched `build_chain`:
- `test_llm_stream.py::test_mock_stream_shape`
- `test_orchestrator.py` (4 sites: asserts "offline stand-in", echo, provider_kind, bad-blob)
- Budget/credits gate tests → Define new error contract instead of mock serving

**Category D: Skip-Mock Guard Tests - DELETE**
- `test_salience_llm.py::test_judge_skips_mock_adapter`
- `test_consolidate.py::test_mock_adapter_is_noop` + `test_era_mock_adapter_is_noop`
- `test_p1_longterm.py::test_relationship_note_mock_and_empty_noop`

**Category E: Web Tests**
- ✅ **No changes needed** - All use fetch-level mocks, unaffected by MockAdapter removal

**Category F: Eval Package**
- ✅ **No changes needed** - `packages/eval/src/ai_companion_eval/mock.py` is independent

## New Behavior

### Before:
Silent fallback to mock when:
- No provider keys configured (zero-config docker compose)
- All real providers fail (429/5xx/timeout)
- Budget hard-stop reached
- Out of hosted credits

### After:
Explicit errors:
- **Empty chain** → `NoProviderAvailableError("No provider configured. Add API key in Settings.")`
- **Budget hard-stop** → **HTTP 402 (Payment Required)** with spent/budget details
- **Out of credits** → **HTTP 402 (Payment Required)** with credits message
- **All providers failed** → Error from last provider (no silent fallback)

## Files Modified

### Backend (Python)
1. `apps/api/src/ai_companion_api/llm/mock_adapter.py` - **DELETED**
2. `apps/api/src/ai_companion_api/llm/provider.py` - MockAdapter removal from build_chain, DEFAULT_MODELS
3. `apps/api/src/ai_companion_api/llm/__init__.py` - Export cleanup
4. `apps/api/src/ai_companion_api/routing/fallback.py` - Mock re-raise removal
5. `apps/api/src/ai_companion_api/routing/router.py` - Mock node removal
6. `apps/api/src/ai_companion_api/routers/llm.py` - Budget gate → HTTP 402
7. `apps/api/src/ai_companion_api/turn/__init__.py` - Messenger path update

### Contracts (TypeScript + Python)
8. `packages/contracts/src/ts/index.ts` - 5 fields tightened to ProviderKind
9. `packages/contracts/src/py/ai_companion_contracts/models.py` - Mirror changes

### Frontend (TypeScript)
10. `apps/web/lib/api-client.ts` - Comment updates
11. `apps/web/components/screens/RoutingScreen.tsx` - Mock case removal
12. `apps/web/components/screens/ChatScreen.tsx` - KIND_LABEL cleanup
13. `apps/web/lib/i18n.tsx` - rt.chain.local removal
14. `apps/web/components/GuestShowcase.tsx` - Mock chip removal

## Next Steps (When Ready)

1. Set up local Python environment with dev dependencies
2. Rewrite Category A-C tests (estimated 2-3 hours)
3. Delete Category D tests
4. Run full test suite: `pytest apps/api/tests/`
5. Verify CI passes

## Impact

- ✅ **Production code**: Complete - MockAdapter fully removed
- ⚠️ **Tests**: Need rewrite (~20+ assertions in 8 files)
- ✅ **Type Safety**: Improved - 5 fields now use proper ProviderKind enum
- ✅ **User Experience**: More honest - explicit errors instead of silent fallback
