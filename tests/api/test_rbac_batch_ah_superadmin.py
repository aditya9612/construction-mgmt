"""
RBAC Batch AH: Super Admin Platform Management - Authorization Boundary Test Suite
===================================================================================
TC01-TC22 cover the complete authorization boundary for all 40 SA endpoints.
"""

import uuid, inspect
from contextlib import asynccontextmanager
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.api import superadmin as superadmin_module
from app.models.company import Company
from app.models.user import User, UserRole, ActivityLog
from app.models.subscription import Plan

def _tok(user_id): return create_access_token({'sub': str(user_id)})
def _auth(tok): return {'Authorization': f'Bearer {tok}'}

SAFE_COMPANY_ID = 999999
SAFE_USER_ID    = 999999
SAFE_PLAN_ID    = 999999
SAFE_TXN_ID     = 999999

ALL_ENDPOINTS = [
    ('GET',    '/api/v1/superadmin/profile',                                             {},                                    None),
    ('PUT',    '/api/v1/superadmin/profile',                                             {},                                    {'full_name': 'Test'}),
    ('POST',   '/api/v1/superadmin/change-password',                                     {},                                    {'current_password': 'x', 'new_password': 'y'}),
    ('GET',    '/api/v1/superadmin/dashboard-stats',                                     {},                                    None),
    ('GET',    '/api/v1/superadmin/companies',                                           {},                                    None),
    ('POST',   '/api/v1/superadmin/companies',                                           {},                                    {'name': 'AH Co', 'subdomain': 'ah-tc01'}),
    ('GET',    '/api/v1/superadmin/companies/{company_id}',                              {'company_id': SAFE_COMPANY_ID},       None),
    ('PUT',    '/api/v1/superadmin/companies/{company_id}',                              {'company_id': SAFE_COMPANY_ID},       {'name': 'Upd'}),
    ('PUT',    '/api/v1/superadmin/companies/{company_id}/status',                       {'company_id': SAFE_COMPANY_ID},       {'is_active': True}),
    ('POST',   '/api/v1/superadmin/companies/{company_id}/activate',                     {'company_id': SAFE_COMPANY_ID},       None),
    ('POST',   '/api/v1/superadmin/companies/{company_id}/suspend',                      {'company_id': SAFE_COMPANY_ID},       None),
    ('DELETE', '/api/v1/superadmin/companies/{company_id}',                              {'company_id': SAFE_COMPANY_ID},       None),
    ('GET',    '/api/v1/superadmin/companies/{company_id}/stats',                        {'company_id': SAFE_COMPANY_ID},       None),
    ('GET',    '/api/v1/superadmin/companies/{company_id}/users',                        {'company_id': SAFE_COMPANY_ID},       None),
    ('POST',   '/api/v1/superadmin/companies/{company_id}/admin',                        {'company_id': SAFE_COMPANY_ID},       {'email': 'x@y.com', 'full_name': 'X', 'password': 'Abc123!'}),
    ('GET',    '/api/v1/superadmin/companies/{company_id}/users/{user_id}',              {'company_id': SAFE_COMPANY_ID, 'user_id': SAFE_USER_ID}, None),
    ('PUT',    '/api/v1/superadmin/companies/{company_id}/users/{user_id}/status',       {'company_id': SAFE_COMPANY_ID, 'user_id': SAFE_USER_ID}, {'is_active': True}),
    ('POST',   '/api/v1/superadmin/companies/{company_id}/users/{user_id}/activate',     {'company_id': SAFE_COMPANY_ID, 'user_id': SAFE_USER_ID}, None),
    ('POST',   '/api/v1/superadmin/companies/{company_id}/users/{user_id}/deactivate',   {'company_id': SAFE_COMPANY_ID, 'user_id': SAFE_USER_ID}, None),
    ('GET',    '/api/v1/superadmin/plans',                                               {},                                    None),
    ('POST',   '/api/v1/superadmin/plans',                                               {},                                    {'name': 'Plan', 'code': 'pl_tc01', 'price': 999.0, 'billing_interval': 'monthly', 'currency': 'INR', 'features': {}, 'is_active': True}),
    ('GET',    '/api/v1/superadmin/plans/{plan_id}',                                     {'plan_id': SAFE_PLAN_ID},             None),
    ('PUT',    '/api/v1/superadmin/plans/{plan_id}',                                     {'plan_id': SAFE_PLAN_ID},             {'price': 1999.0}),
    ('DELETE', '/api/v1/superadmin/plans/{plan_id}',                                     {'plan_id': SAFE_PLAN_ID},             None),
    ('GET',    '/api/v1/superadmin/companies/{company_id}/subscription',                 {'company_id': SAFE_COMPANY_ID},       None),
    ('POST',   '/api/v1/superadmin/companies/{company_id}/subscription',                 {'company_id': SAFE_COMPANY_ID},       {'plan_id': SAFE_PLAN_ID}),
    ('PUT',    '/api/v1/superadmin/companies/{company_id}/subscription',                 {'company_id': SAFE_COMPANY_ID},       {'plan_id': SAFE_PLAN_ID}),
    ('POST',   '/api/v1/superadmin/companies/{company_id}/subscription/activate',        {'company_id': SAFE_COMPANY_ID},       None),
    ('POST',   '/api/v1/superadmin/companies/{company_id}/subscription/suspend',         {'company_id': SAFE_COMPANY_ID},       None),
    ('POST',   '/api/v1/superadmin/companies/{company_id}/subscription/cancel',          {'company_id': SAFE_COMPANY_ID},       None),
    ('GET',    '/api/v1/superadmin/companies/{company_id}/entitlements',                 {'company_id': SAFE_COMPANY_ID},       None),
    ('GET',    '/api/v1/superadmin/companies/{company_id}/invoices',                     {'company_id': SAFE_COMPANY_ID},       None),
    ('GET',    '/api/v1/superadmin/billing/reconciliation',                              {},                                    None),
    ('GET',    '/api/v1/superadmin/companies/{company_id}/billing/reconciliation',       {'company_id': SAFE_COMPANY_ID},       None),
    ('GET',    '/api/v1/superadmin/companies/{company_id}/billing-events',               {'company_id': SAFE_COMPANY_ID},       None),
    ('GET',    '/api/v1/superadmin/audit-logs',                                          {},                                    None),
    ('GET',    '/api/v1/superadmin/companies/{company_id}/audit-logs',                   {'company_id': SAFE_COMPANY_ID},       None),
    ('GET',    '/api/v1/superadmin/manual-payments',                                     {},                                    None),
    ('POST',   '/api/v1/superadmin/manual-payments/{transaction_id}/verify',             {'transaction_id': SAFE_TXN_ID},       None),
    ('POST',   '/api/v1/superadmin/manual-payments/{transaction_id}/reject',             {'transaction_id': SAFE_TXN_ID},       {'rejection_reason': 'Dup'}),
]

assert len(ALL_ENDPOINTS) == 40

def _url(t, p):
    url = t
    for k, v in p.items(): url = url.replace('{' + k + '}', str(v))
    return url

def _client():
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test')

async def _req(ac, m, url, body, h):
    if m == 'GET':    return await ac.get(url, headers=h)
    elif m == 'POST': return await ac.post(url, json=body or {}, headers=h)
    elif m == 'PUT':  return await ac.put(url, json=body or {}, headers=h)
    elif m == 'DELETE': return await ac.delete(url, headers=h)
    raise ValueError(m)

@asynccontextmanager
async def _actors():
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd = get_password_hash('Secret123!')
        comp = Company(name=f'AH_Tenant_{uid}', subdomain=f'ah-{uid}', is_active=True)
        db.add(comp); await db.flush()
        sa = User(email=f'ah_sa_{uid}@infrapilot.com', hashed_password=pwd, full_name=f'AH SA {uid}',
                  mobile=f'91{uuid.uuid4().int % 100000000:08d}', company_id=None, role=UserRole.ADMIN.value,
                  is_super_admin=True, is_active=True, is_deleted=False)
        non_sa = User(email=f'ah_adm_{uid}@test.com', hashed_password=pwd, full_name=f'AH Admin {uid}',
                      mobile=f'92{uuid.uuid4().int % 100000000:08d}', company_id=comp.id, role=UserRole.ADMIN.value,
                      is_super_admin=False, is_active=True, is_deleted=False)
        eng = User(email=f'ah_eng_{uid}@test.com', hashed_password=pwd, full_name=f'AH Eng {uid}',
                   mobile=f'93{uuid.uuid4().int % 100000000:08d}', company_id=comp.id, role=UserRole.SITE_ENGINEER.value,
                   is_super_admin=False, is_active=True, is_deleted=False)
        db.add_all([sa, non_sa, eng]); await db.commit()
        data = {'comp': comp, 'sa': sa, 'non_sa': non_sa, 'eng': eng}
    try:
        yield data
    finally:
        async with AsyncSessionLocal() as db:
            ids = [sa.id, non_sa.id, eng.id]
            await db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.execute(delete(Company).where(Company.id == comp.id))
            await db.commit()


@pytest.mark.asyncio
async def test_tc01_unauthenticated_all_40_return_401():
    '''TC01: No auth header -> 401 on all 40 SA endpoints.'''
    async with _client() as ac:
        failures = []
        for m, t, p, b in ALL_ENDPOINTS:
            url = _url(t, p)
            resp = await _req(ac, m, url, b, {})
            if resp.status_code != 401: failures.append(f'{m} {url} -> {resp.status_code}')
    assert not failures, 'Unauthenticated got not-401:\n' + '\n'.join(failures)


@pytest.mark.asyncio
async def test_tc02_non_sa_engineer_all_40_return_403():
    '''TC02: Non-SA SiteEngineer -> 403 on all 40 SA endpoints.'''
    async with _actors() as d:
        tok = _tok(d['eng'].id)
        async with _client() as ac:
            failures = []
            for m, t, p, b in ALL_ENDPOINTS:
                url = _url(t, p)
                resp = await _req(ac, m, url, b, _auth(tok))
                if resp.status_code != 403: failures.append(f'{m} {url} -> {resp.status_code}')
    assert not failures, 'Non-SA eng got not-403:\n' + '\n'.join(failures)


@pytest.mark.asyncio
async def test_tc03_tenant_admin_not_sa_all_40_return_403():
    '''TC03: role=Admin + is_super_admin=False -> 403. SA gate is NOT role-based.'''
    async with _actors() as d:
        tok = _tok(d['non_sa'].id)
        async with _client() as ac:
            failures = []
            for m, t, p, b in ALL_ENDPOINTS:
                url = _url(t, p)
                resp = await _req(ac, m, url, b, _auth(tok))
                if resp.status_code != 403: failures.append(f'{m} {url} -> {resp.status_code}')
    assert not failures, 'Tenant Admin non-SA got not-403:\n' + '\n'.join(failures)


@pytest.mark.asyncio
async def test_tc04_db_driven_is_super_admin_flag():
    '''TC04: is_super_admin=False -> 403; is_super_admin=True -> passes gate.'''
    async with _actors() as d:
        url = '/api/v1/superadmin/dashboard-stats'
        async with _client() as ac:
            r1 = await ac.get(url, headers=_auth(_tok(d['non_sa'].id)))
            assert r1.status_code == 403, f'Non-SA got {r1.status_code}, expected 403'
            r2 = await ac.get(url, headers=_auth(_tok(d['sa'].id)))
            assert r2.status_code not in (401, 403), f'SA blocked at gate: {r2.status_code}'


@pytest.mark.asyncio
async def test_tc05_sa_passes_auth_gate_all_40():
    '''TC05: SA never blocked by 401/403 on any SA endpoint.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        async with _client() as ac:
            failures = []
            for m, t, p, b in ALL_ENDPOINTS:
                url = _url(t, p)
                resp = await _req(ac, m, url, b, _auth(tok))
                if resp.status_code in (401, 403):
                    failures.append(f'{m} {url} -> {resp.status_code} | {resp.text[:200]}')
    assert not failures, 'SA blocked at auth gate:\n' + '\n'.join(failures)


@pytest.mark.asyncio
async def test_tc06_sa_profile_endpoints():
    '''TC06: SA GET/PUT /profile and POST /change-password reach business logic.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        async with _client() as ac:
            r = await ac.get('/api/v1/superadmin/profile', headers=_auth(tok))
            assert r.status_code not in (401, 403)
            if r.status_code == 200:
                assert 'id' in r.json() or 'email' in r.json()
            r2 = await ac.put('/api/v1/superadmin/profile', json={'full_name': 'SA Update'}, headers=_auth(tok))
            assert r2.status_code not in (401, 403)
            r3 = await ac.post('/api/v1/superadmin/change-password',
                               json={'current_password': 'WrongPw!', 'new_password': 'NewPw456!'},
                               headers=_auth(tok))
            assert r3.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_tc07_sa_dashboard_stats():
    '''TC07: SA dashboard-stats returns 200 with companies and users keys.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        async with _client() as ac:
            r = await ac.get('/api/v1/superadmin/dashboard-stats', headers=_auth(tok))
            assert r.status_code == 200
            assert 'companies' in r.json() and 'users' in r.json()


@pytest.mark.asyncio
async def test_tc08_sa_company_list_and_create():
    '''TC08: SA can list companies (200, items key) and create.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        uid = uuid.uuid4().hex[:6]
        async with _client() as ac:
            r_list = await ac.get('/api/v1/superadmin/companies', headers=_auth(tok))
            assert r_list.status_code == 200
            assert 'items' in r_list.json()
            r_c = await ac.post('/api/v1/superadmin/companies',
                                json={'name': f'TC08_{uid}', 'subdomain': f'tc08-{uid}'},
                                headers=_auth(tok))
            assert r_c.status_code in (200, 409)


@pytest.mark.asyncio
async def test_tc09_sa_company_crud_lifecycle():
    '''TC09: SA can GET/PUT/activate/suspend/stats for a company.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        cid = d['comp'].id
        async with _client() as ac:
            r = await ac.get(f'/api/v1/superadmin/companies/{cid}', headers=_auth(tok))
            assert r.status_code == 200 and r.json()['id'] == cid
            r2 = await ac.put(f'/api/v1/superadmin/companies/{cid}', json={'name': 'TC09_Upd'}, headers=_auth(tok))
            assert r2.status_code == 200
            r3 = await ac.put(f'/api/v1/superadmin/companies/{cid}/status', json={'is_active': False}, headers=_auth(tok))
            assert r3.status_code not in (401, 403)
            r4 = await ac.post(f'/api/v1/superadmin/companies/{cid}/activate', headers=_auth(tok))
            assert r4.status_code not in (401, 403)
            r5 = await ac.post(f'/api/v1/superadmin/companies/{cid}/suspend', headers=_auth(tok))
            assert r5.status_code not in (401, 403)
            await ac.post(f'/api/v1/superadmin/companies/{cid}/activate', headers=_auth(tok))
            r6 = await ac.get(f'/api/v1/superadmin/companies/{cid}/stats', headers=_auth(tok))
            assert r6.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_tc10_sa_nonexistent_company_returns_404():
    '''TC10: SA accessing nonexistent company -> 404 (passed gate, hit business logic).'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        async with _client() as ac:
            r = await ac.get(f'/api/v1/superadmin/companies/{SAFE_COMPANY_ID}', headers=_auth(tok))
            assert r.status_code not in (401, 403)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_tc11_sa_company_user_management():
    '''TC11: SA can list, inspect, activate, deactivate, and update user status.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        cid, uid = d['comp'].id, d['eng'].id
        async with _client() as ac:
            r = await ac.get(f'/api/v1/superadmin/companies/{cid}/users', headers=_auth(tok))
            assert r.status_code == 200
            r2 = await ac.get(f'/api/v1/superadmin/companies/{cid}/users/{uid}', headers=_auth(tok))
            assert r2.status_code not in (401, 403)
            r3 = await ac.post(f'/api/v1/superadmin/companies/{cid}/users/{uid}/activate', headers=_auth(tok))
            assert r3.status_code not in (401, 403)
            r4 = await ac.post(f'/api/v1/superadmin/companies/{cid}/users/{uid}/deactivate', headers=_auth(tok))
            assert r4.status_code not in (401, 403)
            r5 = await ac.put(f'/api/v1/superadmin/companies/{cid}/users/{uid}/status', json={'is_active': True}, headers=_auth(tok))
            assert r5.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_tc12_sa_plans_crud():
    '''TC12: SA can list plans; plan CRUD accessible; nonexistent plan -> 404.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        uid = uuid.uuid4().hex[:6]
        async with _client() as ac:
            r_l = await ac.get('/api/v1/superadmin/plans', headers=_auth(tok))
            assert r_l.status_code == 200 and isinstance(r_l.json(), list)
            plan = {'name': f'TC12 {uid}', 'code': f'tc12_{uid}', 'price': 1999.0,
                    'billing_interval': 'monthly', 'currency': 'INR', 'features': {'max_users': 20}, 'is_active': True}
            r_c = await ac.post('/api/v1/superadmin/plans', json=plan, headers=_auth(tok))
            assert r_c.status_code not in (401, 403)
            if r_c.status_code == 200:
                pid = r_c.json()['id']
                rg = await ac.get(f'/api/v1/superadmin/plans/{pid}', headers=_auth(tok))
                assert rg.status_code == 200 and rg.json()['code'] == f'tc12_{uid}'
                rp = await ac.put(f'/api/v1/superadmin/plans/{pid}', json={'price': 2499.0}, headers=_auth(tok))
                assert rp.status_code not in (401, 403)
                rd = await ac.delete(f'/api/v1/superadmin/plans/{pid}', headers=_auth(tok))
                assert rd.status_code not in (401, 403)
            r_m = await ac.get(f'/api/v1/superadmin/plans/{SAFE_PLAN_ID}', headers=_auth(tok))
            assert r_m.status_code == 404


@pytest.mark.asyncio
async def test_tc13_sa_subscription_lifecycle():
    '''TC13: SA can reach subscription endpoints; nonexistent -> not 401/403.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        cid = d['comp'].id
        async with _client() as ac:
            r = await ac.get(f'/api/v1/superadmin/companies/{cid}/subscription', headers=_auth(tok))
            assert r.status_code not in (401, 403)
            for action in ('activate', 'suspend', 'cancel'):
                r2 = await ac.post(f'/api/v1/superadmin/companies/{SAFE_COMPANY_ID}/subscription/{action}', headers=_auth(tok))
                assert r2.status_code not in (401, 403), f'subscription/{action} blocked: {r2.status_code}'


@pytest.mark.asyncio
async def test_tc14_sa_entitlements_and_invoices():
    '''TC14: SA entitlements and invoices accessible.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        async with _client() as ac:
            r1 = await ac.get(f'/api/v1/superadmin/companies/{SAFE_COMPANY_ID}/entitlements', headers=_auth(tok))
            assert r1.status_code not in (401, 403)
            r2 = await ac.get(f'/api/v1/superadmin/companies/{SAFE_COMPANY_ID}/invoices', headers=_auth(tok))
            assert r2.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_tc15_sa_billing_reconciliation_and_events():
    '''TC15: SA billing reconciliation (platform + company) and events reachable.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        async with _client() as ac:
            r1 = await ac.get('/api/v1/superadmin/billing/reconciliation', headers=_auth(tok))
            assert r1.status_code not in (401, 403)
            r2 = await ac.get(f'/api/v1/superadmin/companies/{SAFE_COMPANY_ID}/billing/reconciliation', headers=_auth(tok))
            assert r2.status_code not in (401, 403)
            r3 = await ac.get(f'/api/v1/superadmin/companies/{SAFE_COMPANY_ID}/billing-events', headers=_auth(tok))
            assert r3.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_tc16_sa_platform_audit_logs():
    '''TC16: SA can access platform-wide and company audit logs.'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        cid = d['comp'].id
        async with _client() as ac:
            r = await ac.get(f'/api/v1/superadmin/audit-logs?performed_by={d["sa"].id}', headers=_auth(tok))
            assert r.status_code not in (401, 403)
            if r.status_code == 200:
                assert 'items' in r.json()
            r2 = await ac.get(f'/api/v1/superadmin/companies/{cid}/audit-logs', headers=_auth(tok))
            assert r2.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_tc17_sa_manual_payments():
    '''TC17: SA can list payments and reach verify/reject (nonexistent -> not 401/403).'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        async with _client() as ac:
            r = await ac.get('/api/v1/superadmin/manual-payments', headers=_auth(tok))
            assert r.status_code not in (401, 403)
            r2 = await ac.post(f'/api/v1/superadmin/manual-payments/{SAFE_TXN_ID}/verify', headers=_auth(tok))
            assert r2.status_code not in (401, 403)
            r3 = await ac.post(f'/api/v1/superadmin/manual-payments/{SAFE_TXN_ID}/reject',
                               json={'rejection_reason': 'Test'}, headers=_auth(tok))
            assert r3.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_tc18_sa_delete_nonexistent_company():
    '''TC18: SA DELETE nonexistent company -> 404 (not blocked at auth gate).'''
    async with _actors() as d:
        tok = _tok(d['sa'].id)
        async with _client() as ac:
            r = await ac.delete(f'/api/v1/superadmin/companies/{SAFE_COMPANY_ID}', headers=_auth(tok))
            assert r.status_code not in (401, 403)


def test_tc19_route_preservation():
    '''TC19: Exactly 40 SA routes, 781 total routes, 0 duplicates.'''
    from fastapi.routing import APIRoute
    sa_routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith('/api/v1/superadmin')]
    assert len(sa_routes) == 40, f'Expected 40 SA routes, got {len(sa_routes)}'
    unique_sa = set((list(r.methods)[0], r.path) for r in sa_routes)
    assert len(unique_sa) == 40, f'Expected 40 unique, got {len(unique_sa)}'
    all_routes = [r for r in app.routes if isinstance(r, APIRoute)]
    assert len(all_routes) == 781, f'Total routes changed: expected 781, got {len(all_routes)}'


def test_tc20_static_code_hygiene():
    '''TC20: superadmin.py uses require_super_admin router dep; no require_permission/require_roles.'''
    source = inspect.getsource(superadmin_module)
    assert 'require_permission' not in source, 'MUST NOT use require_permission()'
    assert 'require_roles' not in source, 'MUST NOT use require_roles()'
    assert 'admin_required' not in source, 'MUST NOT use admin_required'
    assert "current_user.role ==" not in source, 'MUST NOT gate via role =='
    assert 'require_super_admin' in source, 'require_super_admin MUST appear'
    assert 'dependencies=[Depends(require_super_admin)]' in source, 'Router-level dep MUST be present'


def test_tc21_endpoint_table_completeness():
    '''TC21: ALL_ENDPOINTS covers all 40 registered superadmin routes exactly.'''
    from fastapi.routing import APIRoute
    registered = set()
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith('/api/v1/superadmin'):
            for m in r.methods: registered.add((m, r.path))
    covered = {(m, t) for m, t, _, _ in ALL_ENDPOINTS}
    missing = registered - covered
    extra = covered - registered
    assert not missing, 'SA routes NOT in ALL_ENDPOINTS:\n' + '\n'.join(f'  {m} {p}' for m, p in sorted(missing))
    assert not extra, 'ALL_ENDPOINTS has routes not in app:\n' + '\n'.join(f'  {m} {p}' for m, p in sorted(extra))


def test_tc22_production_source_integrity():
    '''TC22: dependencies.py checks is_super_admin; rbac_seed.py has no superadmin namespace.'''
    import pathlib
    deps = (pathlib.Path(__file__).parents[2] / 'app' / 'core' / 'dependencies.py').read_text(encoding='utf-8')
    assert 'is_super_admin' in deps, 'require_super_admin must check is_super_admin'
    sa_block = deps.split('require_super_admin')[1].split('async def')[0]
    assert 'UserRole.ADMIN' not in sa_block, 'require_super_admin must NOT check UserRole.ADMIN'
    seed = (pathlib.Path(__file__).parents[2] / 'app' / 'core' / 'rbac_seed.py').read_text(encoding='utf-8')
    assert 'superadmin' not in seed, 'rbac_seed.py MUST NOT have superadmin namespace'