# RBAC Final Coverage Audit

## 1. Executive Summary

An exhaustive, read-only architectural and security audit of the entire backend application was conducted to determine the exact progress of the Role-Based Access Control (RBAC) migration. 

Every router mount in `app/main.py`, all nested sub-routers, router prefixes, and route dependency graphs were scanned directly from the active FastAPI application instance. 

Key high-level findings:
1. **Total Registered Production API Routes**: Exactly **774 active production routes** are registered under `/api/v1`. Non-production routes (3 health check endpoints `/health`, `/health/live`, `/health/ready`, 4 OpenAPI/docs endpoints, 1 static upload mount, and 1 WebSocket endpoint) were inventoried and excluded.
2. **Current RBAC Completion Baseline**: The reported baseline of completed Batches A–O is **341 routes**. Independent forensic verification reveals that **342 routes** actively enforce database-driven permissions via `require_permission(...)`. An off-by-one undercount error of 1 route originated in the pre-Batch M baseline (reported as 315 instead of 316) and carried through Batches M (+8), N (+6), and O (+12).
3. **Remaining Production API Routes**: Exactly **433 routes** remain unmigrated based on the 341 reported baseline (**432 routes** verified unmigrated).
4. **Current Coverage**: **44.06%** based on the 341 baseline (**44.19%** based on the verified 342 count).
5. **Critical Security Exposures in Remaining Surface**:
   - **22 completely unauthenticated endpoints**, including critical financial journal endpoints in `app/api/journal.py` and cash/bank book import endpoints in `app/api/accountant.py`.
   - **268 routes** still gated behind legacy hardcoded role allowlists (`require_roles`), preventing runtime DB grant/revoke and wildcard override semantics.
   - **92 routes** relying on raw `get_current_active_user` with zero role or permission enforcement (notably `app/api/payroll.py` with 11 unprotected payroll routes).
   - **Severe cross-tenant IDOR** vulnerabilities across `app/api/approval.py` (10 unscoped entity lookups), `app/api/accountant.py` (16 unscoped entity lookups), and `app/api/agreement.py` (zero company scoping).

## 2. Exact Total Production Route Count
- Total active production routes: 774
- Verified completed routes: 342 (Reported baseline: 341)
- Remaining routes: 432 (433 relative to 341 baseline)
- Coverage: 44.19% (44.06% relative to 341 baseline)

## 3. Completed Batches
A–O = 341 routes (Reported baseline; 342 verified active routes across 15 closed batches)

| Batch | Operational Domain | Source File(s) | Verified Routes | Cumulative Routes | Status |
|:---:|:---|:---|:---:|:---:|:---:|
| **Batch A** | Alerts, CAD, Drawings, Notifications, Visualizations, Settings | `alert.py`, `cad.py`, `project.py` (drawing_router), `notification.py`, `project_visualization.py`, `settings.py` | 20 | 20 | **CLOSED** |
| **Batch B** | Attendance & Dashboard | `attendance.py`, `dashboard.py` | 24 | 44 | **CLOSED** |
| **Batch C** | Billing & Expenses | `billing.py`, `expense.py` | 24 | 68 | **CLOSED** |
| **Batch D** | Contractors | `contractor.py` | 15 | 83 | **CLOSED** |
| **Batch E** | Equipment Management | `equipment.py` | 46 | 129 | **CLOSED** |
| **Batch F** | QC, Safety & Checklists | `project.py` (qc_router, safety_router, checklist_router) | 22 | 151 | **CLOSED** |
| **Batch G** | Materials & Procurement | `material.py` | 38 | 189 | **CLOSED** |
| **Batch H** | Labour & Wage Management | `labour.py` | 32 | 221 | **CLOSED** |
| **Batch I** | BOQ (Bill of Quantities) | `boq.py` | 27 | 248 | **CLOSED** |
| **Batch J** | Invoices Management | `invoice.py` | 28 | 276 | **CLOSED** |
| **Batch K** | Quotations Management | `quotation.py` | 27 | 303 | **CLOSED** |
| **Batch L** | Client Payments & Receipts | `client_payment.py` | 13 | 316 | **CLOSED** |
| **Batch M** | Document Management | `document.py` | 8 | 324 | **CLOSED** |
| **Batch N** | Final Measurement Book | `final_measurement.py` | 6 | 330 | **CLOSED** |
| **Batch O** | Owner & Client Management | `owner.py` | 12 | 342 | **CLOSED** |
| **TOTAL** | **All Batches A–O** | **17 Source Files** | **342** | **342** | **CLOSED & VERIFIED** |

## 4. Remaining Modules

| Module | Routes | Status |
|:---|:---:|:---|
| `project` | 89 | LEGACY_ROLES |
| `accountant` | 76 | LEGACY_ROLES, UNAUTHENTICATED |
| `chat` | 45 | RAW_AUTH, UNAUTHENTICATED |
| `reports` | 42 | LEGACY_ROLES |
| `superadmin` | 40 | SUPER_ADMIN_ONLY |
| `master_data` | 18 | LEGACY_ROLES, UNAUTHENTICATED |
| `journal` | 14 | RAW_AUTH, UNAUTHENTICATED |
| `saas_billing` | 14 | RAW_AUTH, TENANT_ADMIN_ONLY, UNAUTHENTICATED |
| `work_update` | 12 | LEGACY_ROLES |
| `user` | 11 | LEGACY_ROLES, RAW_AUTH |
| `payroll` | 11 | RAW_AUTH |
| `rbac` | 9 | LEGACY_ROLES |
| `vendor_bills` | 6 | LEGACY_ROLES |
| `ai` | 5 | LEGACY_ROLES, RAW_AUTH |
| `work_order` | 5 | LEGACY_ROLES |
| `notification` | 5 | RAW_AUTH |
| `auth` | 4 | RAW_AUTH, UNAUTHENTICATED |
| `approval` | 4 | LEGACY_ROLES |
| `settings` | 4 | RAW_AUTH |
| `agreement` | 4 | AD_HOC_PERMS |
| `payments` | 4 | LEGACY_ROLES |
| `dashboard` | 3 | RAW_AUTH |
| `alert` | 3 | RAW_AUTH |
| `attendance` | 3 | RAW_AUTH |
| `material` | 1 | LEGACY_ROLES |
| **TOTAL** | **432** | **Pending Migration** |

## 5. Remaining Route Inventory

| # | Method | Full Path | File | Permission State |
|:---:|:---:|:---|:---|:---|
| 1 | `POST` | `/api/v1/auth/login` | `app/api/auth.py` | UNAUTHENTICATED |
| 2 | `POST` | `/api/v1/auth/verify_otp` | `app/api/auth.py` | UNAUTHENTICATED |
| 3 | `POST` | `/api/v1/auth/logout` | `app/api/auth.py` | RAW_AUTH (get_current_active_user) |
| 4 | `POST` | `/api/v1/auth/logout_all` | `app/api/auth.py` | RAW_AUTH (get_current_active_user) |
| 5 | `POST` | `/api/v1/users/create` | `app/api/user.py` | LEGACY_ROLES (1 roles) |
| 6 | `GET` | `/api/v1/users/me` | `app/api/user.py` | RAW_AUTH (get_current_active_user) |
| 7 | `GET` | `/api/v1/users` | `app/api/user.py` | LEGACY_ROLES (3 roles) |
| 8 | `GET` | `/api/v1/users/roles` | `app/api/user.py` | RAW_AUTH (get_current_active_user) |
| 9 | `PUT` | `/api/v1/users/roles/{role}/status` | `app/api/user.py` | LEGACY_ROLES (1 roles) |
| 10 | `GET` | `/api/v1/users/{user_id}` | `app/api/user.py` | RAW_AUTH (get_current_active_user) |
| 11 | `PUT` | `/api/v1/users/{user_id}` | `app/api/user.py` | LEGACY_ROLES (1 roles) |
| 12 | `DELETE` | `/api/v1/users/{user_id}` | `app/api/user.py` | LEGACY_ROLES (1 roles) |
| 13 | `PUT` | `/api/v1/users/{user_id}/restore` | `app/api/user.py` | LEGACY_ROLES (1 roles) |
| 14 | `GET` | `/api/v1/users/{user_id}/audit-logs` | `app/api/user.py` | LEGACY_ROLES (1 roles) |
| 15 | `GET` | `/api/v1/users/{user_id}/audit-logs-grouped` | `app/api/user.py` | LEGACY_ROLES (1 roles) |
| 16 | `GET` | `/api/v1/rbac/permissions` | `app/api/rbac.py` | LEGACY_ROLES (1 roles) |
| 17 | `GET` | `/api/v1/rbac/roles` | `app/api/rbac.py` | LEGACY_ROLES (1 roles) |
| 18 | `POST` | `/api/v1/rbac/roles` | `app/api/rbac.py` | LEGACY_ROLES (1 roles) |
| 19 | `GET` | `/api/v1/rbac/roles/{role}/permissions` | `app/api/rbac.py` | LEGACY_ROLES (1 roles) |
| 20 | `PUT` | `/api/v1/rbac/roles/{role}/permissions` | `app/api/rbac.py` | LEGACY_ROLES (1 roles) |
| 21 | `GET` | `/api/v1/rbac/users/{user_id}/overrides` | `app/api/rbac.py` | LEGACY_ROLES (1 roles) |
| 22 | `PUT` | `/api/v1/rbac/users/{user_id}/overrides` | `app/api/rbac.py` | LEGACY_ROLES (1 roles) |
| 23 | `POST` | `/api/v1/rbac/seed` | `app/api/rbac.py` | LEGACY_ROLES (1 roles) |
| 24 | `POST` | `/api/v1/rbac/assign-defaults` | `app/api/rbac.py` | LEGACY_ROLES (1 roles) |
| 25 | `GET` | `/api/v1/projects/module-summary` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 26 | `POST` | `/api/v1/projects` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 27 | `GET` | `/api/v1/projects/calendar` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 28 | `GET` | `/api/v1/projects/{project_id}/resource-summary` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 29 | `GET` | `/api/v1/projects/{project_id}/health-score` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 30 | `GET` | `/api/v1/projects` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 31 | `POST` | `/api/v1/projects/{project_id}/schedule` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 32 | `GET` | `/api/v1/projects/{project_id}/schedule` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 33 | `GET` | `/api/v1/projects/{project_id}/progress` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 34 | `GET` | `/api/v1/projects/alerts/projects` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 35 | `GET` | `/api/v1/projects/alerts/tasks` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 36 | `POST` | `/api/v1/projects/{project_id}/members/{user_id}` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 37 | `GET` | `/api/v1/projects/{project_id}/members` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 38 | `DELETE` | `/api/v1/projects/{project_id}/members/{user_id}` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 39 | `GET` | `/api/v1/projects/{project_id}/logs` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 40 | `GET` | `/api/v1/projects/{project_id}/photos` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 41 | `PUT` | `/api/v1/projects/{project_id}/ot-policy` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 42 | `GET` | `/api/v1/projects/{project_id}/profit-loss` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 43 | `POST` | `/api/v1/projects/{project_id}/milestones` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 44 | `GET` | `/api/v1/projects/{project_id}/milestones` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 45 | `GET` | `/api/v1/projects/{project_id}/milestones/{milestone_id}` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 46 | `PUT` | `/api/v1/projects/{project_id}/milestones/{milestone_id}` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 47 | `DELETE` | `/api/v1/projects/{project_id}/milestones/{milestone_id}` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 48 | `POST` | `/api/v1/projects/{project_id}/tasks` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 49 | `GET` | `/api/v1/projects/{project_id}/tasks` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 50 | `GET` | `/api/v1/projects/{project_id}/tasks/{task_id}` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 51 | `PUT` | `/api/v1/projects/{project_id}/tasks/{task_id}` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 52 | `PATCH` | `/api/v1/projects/{project_id}/tasks/{task_id}/status` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 53 | `POST` | `/api/v1/projects/{project_id}/tasks/{task_id}/pass` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 54 | `DELETE` | `/api/v1/projects/{project_id}/tasks/{task_id}` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 55 | `POST` | `/api/v1/projects/{project_id}/tasks/{task_id}/progress` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 56 | `GET` | `/api/v1/projects/{project_id}/tasks/{task_id}/progress` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 57 | `POST` | `/api/v1/projects/task-requests` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 58 | `GET` | `/api/v1/projects/task-requests` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 59 | `GET` | `/api/v1/projects/task-requests` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 60 | `PUT` | `/api/v1/projects/task-requests/{request_id}` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 61 | `DELETE` | `/api/v1/projects/task-requests/{request_id}` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 62 | `POST` | `/api/v1/projects/{project_id}/tasks/{task_id}/comments` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 63 | `GET` | `/api/v1/projects/{project_id}/tasks/{task_id}/comments` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 64 | `GET` | `/api/v1/projects/{project_id}/qr` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 65 | `GET` | `/api/v1/projects/{project_id}` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 66 | `PUT` | `/api/v1/projects/{project_id}` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 67 | `DELETE` | `/api/v1/projects/{project_id}` | `app/api/project.py` | LEGACY_ROLES (1 roles) |
| 68 | `GET` | `/api/v1/projects/{project_id}/gantt` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 69 | `POST` | `/api/v1/site-photos/upload` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 70 | `GET` | `/api/v1/site-photos` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 71 | `DELETE` | `/api/v1/site-photos/{photo_id}` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 72 | `POST` | `/api/v1/materials/ai-recommendation` | `app/api/material.py` | LEGACY_ROLES (5 roles) |
| 73 | `GET` | `/api/v1/master/stats` | `app/api/master_data.py` | UNAUTHENTICATED |
| 74 | `GET` | `/api/v1/master/all` | `app/api/master_data.py` | UNAUTHENTICATED |
| 75 | `GET` | `/api/v1/master/units` | `app/api/master_data.py` | UNAUTHENTICATED |
| 76 | `POST` | `/api/v1/master/units` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 77 | `PUT` | `/api/v1/master/units/{id}` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 78 | `DELETE` | `/api/v1/master/units/{id}` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 79 | `GET` | `/api/v1/master/labour-types` | `app/api/master_data.py` | UNAUTHENTICATED |
| 80 | `POST` | `/api/v1/master/labour-types` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 81 | `PUT` | `/api/v1/master/labour-types/{id}` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 82 | `DELETE` | `/api/v1/master/labour-types/{id}` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 83 | `GET` | `/api/v1/master/activity-types` | `app/api/master_data.py` | UNAUTHENTICATED |
| 84 | `POST` | `/api/v1/master/activity-types` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 85 | `PUT` | `/api/v1/master/activity-types/{id}` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 86 | `DELETE` | `/api/v1/master/activity-types/{id}` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 87 | `GET` | `/api/v1/master/materials` | `app/api/master_data.py` | UNAUTHENTICATED |
| 88 | `POST` | `/api/v1/master/materials` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 89 | `PUT` | `/api/v1/master/materials/{id}` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 90 | `DELETE` | `/api/v1/master/materials/{id}` | `app/api/master_data.py` | LEGACY_ROLES (1 roles) |
| 91 | `POST` | `/api/v1/site-requests` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 92 | `GET` | `/api/v1/site-requests` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 93 | `PUT` | `/api/v1/site-requests/{id}/approve` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 94 | `PUT` | `/api/v1/site-requests/{id}/reject` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 95 | `POST` | `/api/v1/chats/private/{user_id}` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 96 | `POST` | `/api/v1/chats/{chat_id}/messages` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 97 | `POST` | `/api/v1/chats/chat` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 98 | `POST` | `/api/v1/chats/messages/{message_id}/delivered` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 99 | `GET` | `/api/v1/chats/{chat_id}/messages` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 100 | `GET` | `/api/v1/chats/messages/{message_id}/replies` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 101 | `GET` | `/api/v1/chats/pinned` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 102 | `GET` | `/api/v1/chats/{chat_id}/unread` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 103 | `GET` | `/api/v1/chats/messages/{message_id}/reads` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 104 | `POST` | `/api/v1/chats/group` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 105 | `GET` | `/api/v1/chats/users` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 106 | `GET` | `/api/v1/chats/search-users` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 107 | `GET` | `/api/v1/chats/enhanced` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 108 | `DELETE` | `/api/v1/chats/{chat_id}` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 109 | `POST` | `/api/v1/chats/{chat_id}/restore` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 110 | `POST` | `/api/v1/chats/group/{chat_id}/members` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 111 | `DELETE` | `/api/v1/chats/group/{chat_id}/members` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 112 | `GET` | `/api/v1/chats/messages/mentions` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 113 | `GET` | `/api/v1/chats/{chat_id}/mention-users` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 114 | `POST` | `/api/v1/chats/group/{chat_id}/add` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 115 | `POST` | `/api/v1/chats/group/{chat_id}/remove` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 116 | `GET` | `/api/v1/chats/group/{chat_id}/members` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 117 | `PUT` | `/api/v1/chats/group/{chat_id}` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 118 | `GET` | `/api/v1/chats/{chat_id}` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 119 | `POST` | `/api/v1/chats/{chat_id}/mute` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 120 | `POST` | `/api/v1/chats/{chat_id}/archive` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 121 | `POST` | `/api/v1/chats/{chat_id}/typing` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 122 | `GET` | `/api/v1/chats/{chat_id}/typing-users` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 123 | `GET` | `/api/v1/chats/` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 124 | `POST` | `/api/v1/chats/group/{chat_id}/kick` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 125 | `POST` | `/api/v1/chats/group/{chat_id}/leave` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 126 | `POST` | `/api/v1/chats/group/{chat_id}/transfer-admin` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 127 | `POST` | `/api/v1/chats/messages/{message_id}/react` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 128 | `PUT` | `/api/v1/chats/messages/{message_id}/edit` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 129 | `DELETE` | `/api/v1/chats/messages/{message_id}` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 130 | `GET` | `/api/v1/chats/users/{user_id}/status` | `app/api/chat.py` | UNAUTHENTICATED |
| 131 | `GET` | `/api/v1/chats/{chat_id}/search` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 132 | `POST` | `/api/v1/chats/messages/{message_id}/pin` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 133 | `GET` | `/api/v1/chats/{chat_id}/pinned` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 134 | `POST` | `/api/v1/chats/messages/{message_id}/unpin` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 135 | `POST` | `/api/v1/chats/{chat_id}/pin` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 136 | `POST` | `/api/v1/chats/{chat_id}/unpin` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 137 | `POST` | `/api/v1/chats/messages/{message_id}/forward` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 138 | `GET` | `/api/v1/chats/{chat_id}/active-users` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 139 | `GET` | `/api/v1/chats/{chat_id}/user-states` | `app/api/chat.py` | RAW_AUTH (get_current_active_user) |
| 140 | `POST` | `/api/v1/ai/predict` | `app/api/ai.py` | RAW_AUTH (get_current_active_user) |
| 141 | `GET` | `/api/v1/ai` | `app/api/ai.py` | RAW_AUTH (get_current_active_user) |
| 142 | `GET` | `/api/v1/ai/{prediction_id}` | `app/api/ai.py` | RAW_AUTH (get_current_active_user) |
| 143 | `PUT` | `/api/v1/ai/{prediction_id}` | `app/api/ai.py` | LEGACY_ROLES (1 roles) |
| 144 | `DELETE` | `/api/v1/ai/{prediction_id}` | `app/api/ai.py` | LEGACY_ROLES (1 roles) |
| 145 | `POST` | `/api/v1/vendor-bills` | `app/api/vendor_bills.py` | LEGACY_ROLES (2 roles) |
| 146 | `GET` | `/api/v1/vendor-bills` | `app/api/vendor_bills.py` | LEGACY_ROLES (3 roles) |
| 147 | `GET` | `/api/v1/vendor-bills/{id}` | `app/api/vendor_bills.py` | LEGACY_ROLES (3 roles) |
| 148 | `POST` | `/api/v1/vendor-bills/{id}/approve` | `app/api/vendor_bills.py` | LEGACY_ROLES (2 roles) |
| 149 | `POST` | `/api/v1/vendor-bills/{id}/pay` | `app/api/vendor_bills.py` | LEGACY_ROLES (2 roles) |
| 150 | `POST` | `/api/v1/vendor-bills/{bill_id}/reverse-payment/{transaction_id}` | `app/api/vendor_bills.py` | LEGACY_ROLES (2 roles) |
| 151 | `GET` | `/api/v1/dashboard/labour` | `app/api/dashboard.py` | RAW_AUTH (get_current_active_user) |
| 152 | `GET` | `/api/v1/dashboard/labour/payments` | `app/api/dashboard.py` | RAW_AUTH (get_current_active_user) |
| 153 | `GET` | `/api/v1/dashboard/labour/payments/export` | `app/api/dashboard.py` | RAW_AUTH (get_current_active_user) |
| 154 | `POST` | `/api/v1/dsr` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 155 | `GET` | `/api/v1/dsr/project/{project_id}` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 156 | `GET` | `/api/v1/dsr/{id}` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 157 | `PUT` | `/api/v1/dsr/{id}` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 158 | `GET` | `/api/v1/dsr/project/{project_id}/map` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 159 | `GET` | `/api/v1/dsr/project/{project_id}/analytics/labour` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 160 | `GET` | `/api/v1/dsr/project/{project_id}/analytics/contractor` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 161 | `DELETE` | `/api/v1/dsr/{id}` | `app/api/project.py` | LEGACY_ROLES (1 roles) |
| 162 | `GET` | `/api/v1/dsr/{dsr_id}/photos` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 163 | `DELETE` | `/api/v1/dsr/photo/{photo_id}` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 164 | `GET` | `/api/v1/dsr/project/{project_id}/export` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 165 | `PUT` | `/api/v1/dsr/{id}/submit` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 166 | `PUT` | `/api/v1/dsr/{id}/approve` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 167 | `PUT` | `/api/v1/dsr/{id}/reject` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 168 | `GET` | `/api/v1/dsr/project/{project_id}/analytics/issues` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 169 | `POST` | `/api/v1/issues` | `app/api/project.py` | LEGACY_ROLES (4 roles) |
| 170 | `GET` | `/api/v1/issues` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 171 | `GET` | `/api/v1/issues/project/{project_id}` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 172 | `GET` | `/api/v1/issues/{id}` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 173 | `PUT` | `/api/v1/issues/{id}` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 174 | `DELETE` | `/api/v1/issues/{id}` | `app/api/project.py` | LEGACY_ROLES (1 roles) |
| 175 | `GET` | `/api/v1/issues/project/{project_id}` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 176 | `POST` | `/api/v1/approvals` | `app/api/approval.py` | LEGACY_ROLES (7 roles) |
| 177 | `GET` | `/api/v1/approvals` | `app/api/approval.py` | LEGACY_ROLES (7 roles) |
| 178 | `PUT` | `/api/v1/approvals/{id}/approve` | `app/api/approval.py` | LEGACY_ROLES (7 roles) |
| 179 | `PUT` | `/api/v1/approvals/{id}/reject` | `app/api/approval.py` | LEGACY_ROLES (7 roles) |
| 180 | `POST` | `/api/v1/accountant/accounts` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 181 | `GET` | `/api/v1/accountant/accounts` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 182 | `GET` | `/api/v1/accountant/accounts/tree` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 183 | `GET` | `/api/v1/accountant/accounts/export` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 184 | `POST` | `/api/v1/accountant/accounts/import` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 185 | `GET` | `/api/v1/accountant/accounts/{id}` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 186 | `PATCH` | `/api/v1/accountant/accounts/{id}` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 187 | `DELETE` | `/api/v1/accountant/accounts/{id}` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 188 | `GET` | `/api/v1/accountant/accounts/{id}/ledger` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 189 | `POST` | `/api/v1/accountant/receipts` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 190 | `GET` | `/api/v1/accountant/receipts` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 191 | `GET` | `/api/v1/accountant/receipts/summary` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 192 | `GET` | `/api/v1/accountant/payables` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 193 | `POST` | `/api/v1/accountant/payables/{ra_id}/pay` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 194 | `GET` | `/api/v1/accountant/transactions` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 195 | `GET` | `/api/v1/accountant/payables/summary` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 196 | `GET` | `/api/v1/accountant/payables/date-range` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 197 | `POST` | `/api/v1/accountant/bank-accounts` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 198 | `GET` | `/api/v1/accountant/bank-accounts` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 199 | `GET` | `/api/v1/accountant/bank-accounts/export` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 200 | `POST` | `/api/v1/accountant/bank-accounts/import` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 201 | `GET` | `/api/v1/accountant/bank-accounts/{id}` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 202 | `PATCH` | `/api/v1/accountant/bank-accounts/{id}` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 203 | `GET` | `/api/v1/accountant/bank-accounts/{id}/ledger` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 204 | `GET` | `/api/v1/accountant/cash-book/ledger` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 205 | `GET` | `/api/v1/accountant/cash-book/export` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 206 | `POST` | `/api/v1/accountant/cash-book/import` | `app/api/accountant.py` | UNAUTHENTICATED |
| 207 | `POST` | `/api/v1/accountant/petty-cash/transactions` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 208 | `GET` | `/api/v1/accountant/petty-cash/ledger` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 209 | `GET` | `/api/v1/accountant/bank-book/ledger` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 210 | `GET` | `/api/v1/accountant/bank-book/export` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 211 | `POST` | `/api/v1/accountant/bank-book/import` | `app/api/accountant.py` | UNAUTHENTICATED |
| 212 | `POST` | `/api/v1/accountant/journal` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 213 | `GET` | `/api/v1/accountant/journal` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 214 | `GET` | `/api/v1/accountant/gst/summary` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 215 | `GET` | `/api/v1/accountant/bank/summary` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 216 | `GET` | `/api/v1/accountant/assets` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 217 | `GET` | `/api/v1/accountant/assets/{id}` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 218 | `POST` | `/api/v1/accountant/assets` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 219 | `GET` | `/api/v1/accountant/assets/{id}/qr` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 220 | `POST` | `/api/v1/accountant/assets/{id}/depreciate` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 221 | `GET` | `/api/v1/accountant/reports/trial-balance` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 222 | `GET` | `/api/v1/accountant/reports/balance-sheet` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 223 | `POST` | `/api/v1/accountant/offers` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 224 | `GET` | `/api/v1/accountant/offers/{offer_id}/generate` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 225 | `GET` | `/api/v1/accountant/offers/{offer_id}/pdf` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 226 | `POST` | `/api/v1/accountant/bank/reconciliation/import` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 227 | `POST` | `/api/v1/accountant/bank/reconciliation/run` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 228 | `POST` | `/api/v1/accountant/bank/transactions` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 229 | `GET` | `/api/v1/accountant/bank/reconciliation/pending` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 230 | `POST` | `/api/v1/accountant/bank/reconciliation/{transaction_id}/match/{journal_id}` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 231 | `GET` | `/api/v1/accountant/bank/reconciliation/history` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 232 | `GET` | `/api/v1/accountant/bank/reconciliation/export` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 233 | `GET` | `/api/v1/accountant/bank/reconciliation/dashboard` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 234 | `POST` | `/api/v1/accountant/transfers` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 235 | `GET` | `/api/v1/accountant/transfers` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 236 | `POST` | `/api/v1/accountant/gst/returns` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 237 | `GET` | `/api/v1/accountant/gst/returns` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 238 | `GET` | `/api/v1/accountant/tds/deductions` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 239 | `POST` | `/api/v1/accountant/tds/deductions` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 240 | `GET` | `/api/v1/accountant/tds/deductions/{id}` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 241 | `PATCH` | `/api/v1/accountant/tds/deductions/{id}` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 242 | `DELETE` | `/api/v1/accountant/tds/deductions/{id}` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 243 | `GET` | `/api/v1/accountant/gst/invoice-register` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 244 | `GET` | `/api/v1/accountant/gst/returns/generate` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 245 | `POST` | `/api/v1/accountant/gst/reconciliation/match` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 246 | `POST` | `/api/v1/accountant/gst/returns` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 247 | `GET` | `/api/v1/accountant/gst/returns` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 248 | `GET` | `/api/v1/accountant/gst/invoice-register` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 249 | `GET` | `/api/v1/accountant/gst/returns/generate` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 250 | `POST` | `/api/v1/accountant/gst/reconciliation/match` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 251 | `GET` | `/api/v1/accountant/gst/returns/{id}` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 252 | `PATCH` | `/api/v1/accountant/gst/returns/{id}` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 253 | `DELETE` | `/api/v1/accountant/gst/returns/{id}` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 254 | `GET` | `/api/v1/accountant/gst/export` | `app/api/accountant.py` | LEGACY_ROLES (3 roles) |
| 255 | `POST` | `/api/v1/accountant/gst/import` | `app/api/accountant.py` | LEGACY_ROLES (2 roles) |
| 256 | `POST` | `/api/v1/work-orders` | `app/api/work_order.py` | LEGACY_ROLES (2 roles) |
| 257 | `GET` | `/api/v1/work-orders` | `app/api/work_order.py` | LEGACY_ROLES (5 roles) |
| 258 | `GET` | `/api/v1/work-orders/{id}` | `app/api/work_order.py` | LEGACY_ROLES (5 roles) |
| 259 | `PUT` | `/api/v1/work-orders/{id}` | `app/api/work_order.py` | LEGACY_ROLES (2 roles) |
| 260 | `DELETE` | `/api/v1/work-orders/{id}` | `app/api/work_order.py` | LEGACY_ROLES (2 roles) |
| 261 | `POST` | `/api/v1/work-progress/activities` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 262 | `GET` | `/api/v1/work-progress/activities` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 263 | `GET` | `/api/v1/work-progress/activities/{activity_id}` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 264 | `PUT` | `/api/v1/work-progress/activities/{activity_id}` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 265 | `DELETE` | `/api/v1/work-progress/activities/{activity_id}` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 266 | `POST` | `/api/v1/work-progress/daily-entry` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 267 | `GET` | `/api/v1/work-progress/daily-entry` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 268 | `PUT` | `/api/v1/work-progress/daily-entry/{id}` | `app/api/project.py` | LEGACY_ROLES (3 roles) |
| 269 | `DELETE` | `/api/v1/work-progress/daily-entry/{id}` | `app/api/project.py` | LEGACY_ROLES (2 roles) |
| 270 | `GET` | `/api/v1/work-progress/work-order/{work_order_id}/progress-summary` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 271 | `GET` | `/api/v1/work-progress/site-engineer/today-progress` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 272 | `GET` | `/api/v1/work-progress/progress-history` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 273 | `GET` | `/api/v1/work-progress/project/{project_id}/summary` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 274 | `GET` | `/api/v1/work-progress/project/{project_id}/delayed-activities` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 275 | `GET` | `/api/v1/work-progress/reports/pdf` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 276 | `GET` | `/api/v1/work-progress/reports/excel` | `app/api/project.py` | LEGACY_ROLES (7 roles) |
| 277 | `GET` | `/api/v1/reports/projects/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 278 | `GET` | `/api/v1/reports/projects/pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 279 | `GET` | `/api/v1/reports/audit/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 280 | `GET` | `/api/v1/reports/procurement-efficiency` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 281 | `GET` | `/api/v1/reports/audit/pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 282 | `GET` | `/api/v1/reports/assets/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 283 | `GET` | `/api/v1/reports/assets/pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 284 | `GET` | `/api/v1/reports/issues/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 285 | `GET` | `/api/v1/reports/issues/pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 286 | `GET` | `/api/v1/reports/finance/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 287 | `GET` | `/api/v1/reports/finance/pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 288 | `GET` | `/api/v1/reports/profit-loss/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 289 | `GET` | `/api/v1/reports/profit-loss/pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 290 | `GET` | `/api/v1/reports/daily` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 291 | `GET` | `/api/v1/reports/daily/export/pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 292 | `GET` | `/api/v1/reports/weekly` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 293 | `GET` | `/api/v1/reports/labour` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 294 | `GET` | `/api/v1/reports/labour-distribution/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 295 | `GET` | `/api/v1/reports/labour-distribution/pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 296 | `GET` | `/api/v1/reports/material` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 297 | `GET` | `/api/v1/reports/material/export/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 298 | `GET` | `/api/v1/reports/issues` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 299 | `GET` | `/api/v1/reports/issues/export/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 300 | `GET` | `/api/v1/reports/download` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 301 | `GET` | `/api/v1/reports/combined` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 302 | `GET` | `/api/v1/reports/contractor-performance` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 303 | `GET` | `/api/v1/reports/profit-loss` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 304 | `GET` | `/api/v1/reports/project/{project_id}` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 305 | `GET` | `/api/v1/reports/cashflow` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 306 | `GET` | `/api/v1/reports/assets` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 307 | `GET` | `/api/v1/reports/financial-summary` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 308 | `GET` | `/api/v1/reports/quarterly-audit-summary` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 309 | `GET` | `/api/v1/reports/work-summary` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 310 | `GET` | `/api/v1/reports/audit-pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 311 | `GET` | `/api/v1/reports/project` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 312 | `GET` | `/api/v1/reports/project/export/pdf` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 313 | `GET` | `/api/v1/reports/project/export/excel` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 314 | `GET` | `/api/v1/reports/business-intelligence` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 315 | `GET` | `/api/v1/reports/work-category` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 316 | `GET` | `/api/v1/reports/audit-summary` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 317 | `GET` | `/api/v1/reports/commercial-execution` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 318 | `GET` | `/api/v1/reports/contractor-execution` | `app/api/reports.py` | LEGACY_ROLES (7 roles) |
| 319 | `GET` | `/api/v1/alerts` | `app/api/alert.py` | RAW_AUTH (get_current_active_user) |
| 320 | `PUT` | `/api/v1/alerts/{id}/read` | `app/api/alert.py` | RAW_AUTH (get_current_active_user) |
| 321 | `DELETE` | `/api/v1/alerts/{id}` | `app/api/alert.py` | RAW_AUTH (get_current_active_user) |
| 322 | `GET` | `/api/v1/settings` | `app/api/settings.py` | RAW_AUTH (get_current_active_user) |
| 323 | `PUT` | `/api/v1/settings` | `app/api/settings.py` | RAW_AUTH (get_current_active_user) |
| 324 | `PUT` | `/api/v1/settings/profile` | `app/api/settings.py` | RAW_AUTH (get_current_active_user) |
| 325 | `GET` | `/api/v1/settings/profile` | `app/api/settings.py` | RAW_AUTH (get_current_active_user) |
| 326 | `GET` | `/api/v1/agreements/` | `app/api/agreement.py` | AD_HOC_PERMS (agreements.view) |
| 327 | `POST` | `/api/v1/agreements/` | `app/api/agreement.py` | AD_HOC_PERMS (agreements.create) |
| 328 | `GET` | `/api/v1/agreements/stats` | `app/api/agreement.py` | AD_HOC_PERMS (agreements.view) |
| 329 | `GET` | `/api/v1/agreements/{agreement_id}/download` | `app/api/agreement.py` | AD_HOC_PERMS (agreements.view) |
| 330 | `POST` | `/api/v1/attendance/check-in` | `app/api/attendance.py` | RAW_AUTH (get_current_active_user) |
| 331 | `PUT` | `/api/v1/attendance/check-out/{attendance_id}` | `app/api/attendance.py` | RAW_AUTH (get_current_active_user) |
| 332 | `GET` | `/api/v1/attendance/today` | `app/api/attendance.py` | RAW_AUTH (get_current_active_user) |
| 333 | `GET` | `/api/v1/notifications` | `app/api/notification.py` | RAW_AUTH (get_current_active_user) |
| 334 | `GET` | `/api/v1/notifications/unread-count` | `app/api/notification.py` | RAW_AUTH (get_current_active_user) |
| 335 | `PUT` | `/api/v1/notifications/read-all` | `app/api/notification.py` | RAW_AUTH (get_current_active_user) |
| 336 | `PUT` | `/api/v1/notifications/{id}/read` | `app/api/notification.py` | RAW_AUTH (get_current_active_user) |
| 337 | `DELETE` | `/api/v1/notifications/{id}` | `app/api/notification.py` | RAW_AUTH (get_current_active_user) |
| 338 | `GET` | `/api/v1/accountant/payroll/summary` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 339 | `GET` | `/api/v1/accountant/payroll/payslip/export` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 340 | `GET` | `/api/v1/accountant/payroll/staff/register` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 341 | `POST` | `/api/v1/accountant/payroll/staff/process` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 342 | `GET` | `/api/v1/accountant/payroll/staff/history` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 343 | `GET` | `/api/v1/accountant/payroll/labour/wages` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 344 | `GET` | `/api/v1/accountant/payroll/contractor/bills` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 345 | `GET` | `/api/v1/accountant/payroll/staff/export` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 346 | `GET` | `/api/v1/accountant/payroll/contractor/export` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 347 | `GET` | `/api/v1/accountant/payroll/register/export` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 348 | `GET` | `/api/v1/accountant/payroll/register` | `app/api/payroll.py` | RAW_AUTH (get_current_active_user) |
| 349 | `POST` | `/api/v1/journal/manual` | `app/api/journal.py` | RAW_AUTH (get_current_active_user) |
| 350 | `GET` | `/api/v1/journal/manual` | `app/api/journal.py` | UNAUTHENTICATED |
| 351 | `GET` | `/api/v1/journal/manual/{id}` | `app/api/journal.py` | UNAUTHENTICATED |
| 352 | `POST` | `/api/v1/journal/adjustment` | `app/api/journal.py` | RAW_AUTH (get_current_active_user) |
| 353 | `GET` | `/api/v1/journal/adjustment` | `app/api/journal.py` | UNAUTHENTICATED |
| 354 | `GET` | `/api/v1/journal/adjustment/export` | `app/api/journal.py` | UNAUTHENTICATED |
| 355 | `POST` | `/api/v1/journal/adjustment/import` | `app/api/journal.py` | RAW_AUTH (get_current_active_user) |
| 356 | `GET` | `/api/v1/journal/adjustment/{id}` | `app/api/journal.py` | UNAUTHENTICATED |
| 357 | `POST` | `/api/v1/journal/recurring` | `app/api/journal.py` | RAW_AUTH (get_current_active_user) |
| 358 | `GET` | `/api/v1/journal/recurring` | `app/api/journal.py` | UNAUTHENTICATED |
| 359 | `GET` | `/api/v1/journal/recurring/export` | `app/api/journal.py` | UNAUTHENTICATED |
| 360 | `POST` | `/api/v1/journal/recurring/run-due` | `app/api/journal.py` | RAW_AUTH (get_current_active_user) |
| 361 | `POST` | `/api/v1/journal/recurring/{recurring_id}/toggle` | `app/api/journal.py` | UNAUTHENTICATED |
| 362 | `GET` | `/api/v1/journal/export` | `app/api/journal.py` | UNAUTHENTICATED |
| 363 | `POST` | `/api/v1/work-updates` | `app/api/work_update.py` | LEGACY_ROLES (5 roles) |
| 364 | `GET` | `/api/v1/work-updates/my` | `app/api/work_update.py` | LEGACY_ROLES (6 roles) |
| 365 | `GET` | `/api/v1/work-updates/project/{project_id}/timeline` | `app/api/work_update.py` | LEGACY_ROLES (6 roles) |
| 366 | `GET` | `/api/v1/work-updates/export` | `app/api/work_update.py` | LEGACY_ROLES (6 roles) |
| 367 | `GET` | `/api/v1/work-updates/{work_update_id:int}` | `app/api/work_update.py` | LEGACY_ROLES (6 roles) |
| 368 | `POST` | `/api/v1/work-updates/{work_update_id}/before-image` | `app/api/work_update.py` | LEGACY_ROLES (5 roles) |
| 369 | `PUT` | `/api/v1/work-updates/{work_update_id:int}` | `app/api/work_update.py` | LEGACY_ROLES (5 roles) |
| 370 | `POST` | `/api/v1/work-updates/{work_update_id:int}/after-image` | `app/api/work_update.py` | LEGACY_ROLES (5 roles) |
| 371 | `POST` | `/api/v1/work-updates/{work_update_id:int}/submit` | `app/api/work_update.py` | LEGACY_ROLES (5 roles) |
| 372 | `DELETE` | `/api/v1/work-updates/{work_update_id:int}` | `app/api/work_update.py` | LEGACY_ROLES (3 roles) |
| 373 | `DELETE` | `/api/v1/work-updates/images/{image_id}` | `app/api/work_update.py` | LEGACY_ROLES (5 roles) |
| 374 | `PUT` | `/api/v1/work-updates/images/{image_id}` | `app/api/work_update.py` | LEGACY_ROLES (5 roles) |
| 375 | `POST` | `/api/v1/payments/vouchers` | `app/api/payments.py` | LEGACY_ROLES (3 roles) |
| 376 | `GET` | `/api/v1/payments/vouchers` | `app/api/payments.py` | LEGACY_ROLES (4 roles) |
| 377 | `POST` | `/api/v1/payments/vouchers/{id}/mark-paid` | `app/api/payments.py` | LEGACY_ROLES (3 roles) |
| 378 | `POST` | `/api/v1/payments/vouchers/{id}/cancel` | `app/api/payments.py` | LEGACY_ROLES (3 roles) |
| 379 | `GET` | `/api/v1/superadmin/profile` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 380 | `PUT` | `/api/v1/superadmin/profile` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 381 | `POST` | `/api/v1/superadmin/change-password` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 382 | `GET` | `/api/v1/superadmin/dashboard-stats` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 383 | `GET` | `/api/v1/superadmin/companies` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 384 | `POST` | `/api/v1/superadmin/companies` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 385 | `GET` | `/api/v1/superadmin/companies/{company_id}` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 386 | `PUT` | `/api/v1/superadmin/companies/{company_id}` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 387 | `PUT` | `/api/v1/superadmin/companies/{company_id}/status` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 388 | `POST` | `/api/v1/superadmin/companies/{company_id}/activate` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 389 | `POST` | `/api/v1/superadmin/companies/{company_id}/suspend` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 390 | `DELETE` | `/api/v1/superadmin/companies/{company_id}` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 391 | `GET` | `/api/v1/superadmin/companies/{company_id}/stats` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 392 | `GET` | `/api/v1/superadmin/companies/{company_id}/users` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 393 | `POST` | `/api/v1/superadmin/companies/{company_id}/admin` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 394 | `GET` | `/api/v1/superadmin/companies/{company_id}/users/{user_id}` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 395 | `PUT` | `/api/v1/superadmin/companies/{company_id}/users/{user_id}/status` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 396 | `POST` | `/api/v1/superadmin/companies/{company_id}/users/{user_id}/activate` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 397 | `POST` | `/api/v1/superadmin/companies/{company_id}/users/{user_id}/deactivate` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 398 | `GET` | `/api/v1/superadmin/plans` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 399 | `POST` | `/api/v1/superadmin/plans` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 400 | `GET` | `/api/v1/superadmin/plans/{plan_id}` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 401 | `PUT` | `/api/v1/superadmin/plans/{plan_id}` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 402 | `DELETE` | `/api/v1/superadmin/plans/{plan_id}` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 403 | `GET` | `/api/v1/superadmin/companies/{company_id}/subscription` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 404 | `POST` | `/api/v1/superadmin/companies/{company_id}/subscription` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 405 | `PUT` | `/api/v1/superadmin/companies/{company_id}/subscription` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 406 | `POST` | `/api/v1/superadmin/companies/{company_id}/subscription/activate` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 407 | `POST` | `/api/v1/superadmin/companies/{company_id}/subscription/suspend` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 408 | `POST` | `/api/v1/superadmin/companies/{company_id}/subscription/cancel` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 409 | `GET` | `/api/v1/superadmin/companies/{company_id}/entitlements` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 410 | `GET` | `/api/v1/superadmin/companies/{company_id}/invoices` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 411 | `GET` | `/api/v1/superadmin/billing/reconciliation` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 412 | `GET` | `/api/v1/superadmin/companies/{company_id}/billing/reconciliation` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 413 | `GET` | `/api/v1/superadmin/companies/{company_id}/billing-events` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 414 | `GET` | `/api/v1/superadmin/audit-logs` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 415 | `GET` | `/api/v1/superadmin/companies/{company_id}/audit-logs` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 416 | `GET` | `/api/v1/superadmin/manual-payments` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 417 | `POST` | `/api/v1/superadmin/manual-payments/{transaction_id}/verify` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 418 | `POST` | `/api/v1/superadmin/manual-payments/{transaction_id}/reject` | `app/api/superadmin.py` | SUPER_ADMIN_ONLY |
| 419 | `POST` | `/api/v1/saas-billing/checkout` | `app/api/saas_billing.py` | RAW_AUTH (get_current_active_user) |
| 420 | `POST` | `/api/v1/saas-billing/webhook` | `app/api/saas_billing.py` | UNAUTHENTICATED |
| 421 | `GET` | `/api/v1/saas-billing/me` | `app/api/saas_billing.py` | RAW_AUTH (get_current_active_user) |
| 422 | `GET` | `/api/v1/saas-billing/usage` | `app/api/saas_billing.py` | RAW_AUTH (get_current_active_user) |
| 423 | `GET` | `/api/v1/saas-billing/plans` | `app/api/saas_billing.py` | UNAUTHENTICATED |
| 424 | `GET` | `/api/v1/saas-billing/invoices` | `app/api/saas_billing.py` | RAW_AUTH (get_current_active_user) |
| 425 | `GET` | `/api/v1/saas-billing/invoices/{invoice_id}` | `app/api/saas_billing.py` | RAW_AUTH (get_current_active_user) |
| 426 | `GET` | `/api/v1/saas-billing/history` | `app/api/saas_billing.py` | RAW_AUTH (get_current_active_user) |
| 427 | `GET` | `/api/v1/saas-billing/upi/qr-code` | `app/api/saas_billing.py` | TENANT_ADMIN_ONLY |
| 428 | `GET` | `/api/v1/saas-billing/upi/qr-image` | `app/api/saas_billing.py` | TENANT_ADMIN_ONLY |
| 429 | `GET` | `/api/v1/saas-billing/upi/checkout-preview` | `app/api/saas_billing.py` | TENANT_ADMIN_ONLY |
| 430 | `POST` | `/api/v1/saas-billing/upi/submit` | `app/api/saas_billing.py` | TENANT_ADMIN_ONLY |
| 431 | `GET` | `/api/v1/saas-billing/upi/transactions` | `app/api/saas_billing.py` | TENANT_ADMIN_ONLY |
| 432 | `GET` | `/api/v1/saas-billing/upi/transactions/{reference}` | `app/api/saas_billing.py` | TENANT_ADMIN_ONLY |

## 6. RBAC Architecture Findings

A comprehensive architectural review of the entire authorization engine identified the following systemic patterns:

1. **Hardcoded Role Allowlist Legacy Dependency (`require_roles`)**:
   - **268 production routes** still invoke `require_roles(allowed_roles)`.
   - In `app/core/dependencies.py:430-459`, `require_roles` delegates to `require_permission(permission)` ONLY if a permission parameter is explicitly passed. When omitted, it enforces hardcoded strings against `current_user.role`.
   - Impact: These 268 routes are completely immune to dynamic database role-permission grants/revokes and user-level overrides. Granting a permission in `role_permissions` or `user_permission_overrides` has zero effect on these endpoints.
2. **Raw Authentication-Only Routes (`get_current_active_user`)**:
   - **92 production routes** verify ONLY that a valid JWT token exists and the user account is active, with zero role or permission constraints.
   - Crucially, this includes sensitive financial modules: all **11 routes in `app/api/payroll.py`** (wage processing, salary slips, payroll summary) and **44 routes in `app/api/chat.py`**.
3. **Platform-Level Super Admin Dependency (`require_super_admin`)**:
   - **40 production routes** in `app/api/superadmin.py` enforce `require_super_admin(current_user: User)`.
   - Canonical implementation `current_user.is_super_admin is True` is properly centralized. These are platform management routes rather than tenant business routes.
4. **Tenant Admin Custom Dependency (`require_tenant_admin`)**:
   - **6 production routes** in `app/api/saas_billing.py` enforce `require_tenant_admin(current_user: User)`.
   - This check asserts `current_user.role == UserRole.ADMIN.value` and `company_id is not None`. It should eventually be transitioned to `billing.manage` / `billing.edit` canonical permissions.
5. **Ad-Hoc Permission Check Plural Variant (`require_permissions`)**:
   - **4 routes** in `app/api/agreement.py` use `d.require_permissions(["agreements.view"])`.
   - While database permissions are checked, this module was never part of an audited batch, lacks automated batch regression tests, and exhibits critical P0 cross-tenant IDOR vulnerabilities.
6. **Wildcard & Override Compatibility**:
   - The Phase 1 engine (`has_permission`) natively supports global wildcard (`*`) and module wildcard (`module.*`).
   - User positive and negative overrides (`user_permission_overrides`) function correctly for all 342 migrated routes, but cannot operate on the 268 `require_roles` routes or 92 raw auth routes.
7. **Unauthenticated Routes Exposure**:
   - **22 active routes** under `/api/v1` have no authentication dependency whatsoever.

## 7. Tenant / IDOR Findings

A security and multi-tenant boundary audit was performed across all remaining unmigrated modules.

### Top P0 Findings (Critical Data Exposure / Cross-Tenant Tampering)
1. **P0-1: Completely Unauthenticated Financial Journal Endpoints (`app/api/journal.py`)**:
   - 9 endpoints (`GET /manual`, `GET /manual/{id}`, `GET /adjustment`, `GET /adjustment/export`, `GET /adjustment/{id}`, `GET /recurring`, `GET /recurring/export`, `POST /recurring/{recurring_id}/toggle`, `GET /export`) lack any authentication dependency.
   - Any unauthenticated public requester can dump full company general journals, download Excel journal exports, and toggle recurring financial entries.
2. **P0-2: Unauthenticated Cash & Bank Book Import (`app/api/accountant.py`)**:
   - `POST /api/v1/accountant/cash-book/import` and `POST /api/v1/accountant/bank-book/import` have zero authentication dependencies.
   - Anonymous clients can post CSV/Excel payloads directly into the company financial book.
3. **P0-3: Cross-Tenant Approval Bypass IDOR (`app/api/approval.py`)**:
   - Handlers perform unscoped `await db.get(...)` calls across 9 critical business models (`FinalMeasurement`, `BOQ`, `JournalEntry`, `DrawingDocument`, `RABill`, `PurchaseOrder`, `Document`, `Project`, `Approval`).
   - No validation asserts that the referenced entity belongs to `current_user.company_id`. An approver in Company A can approve, reject, or modify bills and purchase orders of Company B.
4. **P0-4: Platform-Wide Cross-Tenant Agreement Disclosure (`app/api/agreement.py`)**:
   - Handlers execute `select(Agreement)` with joins on `Project` and `Owner` without filtering by `current_user.company_id`.
   - Any authenticated user can list, view, and stream contracts and legal agreements belonging to foreign companies.
5. **P0-5: Accountant Bank Account & Tax Filing IDOR (`app/api/accountant.py`)**:
   - 16 distinct `db.get` calls fetch `BankAccount`, `BankTransaction`, `JournalEntry`, `FixedAsset`, `RABill`, `GSTReturn`, and `TDSDeduction` by primary key without checking `company_id`.
   - An accountant in one tenant can inspect bank accounts, modify tax deductions, and alter fixed assets of another tenant.

### Top P1 Findings (High Privilege Escalation / Sensitive Data Leakage)
1. **P1-1: Complete Absence of RBAC on Payroll & Wages (`app/api/payroll.py`)**:
   - All 11 routes rely on raw `get_current_active_user`. Any authenticated user (including site labourers or technicians) can view confidential salary structures, process payroll, and export company payroll reports.
2. **P1-2: Cross-Tenant User Deactivation & Audit Leakage (`app/api/user.py`)**:
   - `PUT /api/v1/users/roles/{role}/status` allows updating role statuses without verifying tenant boundaries.
   - `GET /api/v1/users/{user_id}/audit-logs` and `restore_user` lack strict `company_id` assertion, allowing cross-tenant activity log inspection.
3. **P1-3: Cross-Tenant Chat & Direct Message Leakage (`app/api/chat.py`)**:
   - 18 `db.get` calls on `ChatMessage` and `ChatSession` lack multi-tenant isolation, and WebSocket `/ws/{chat_id}` has no tenant verification guard.

## 8. Permission Catalog Findings

The active database permission catalog (`permissions` table) was directly queried against all unmigrated modules.

### Current Catalog Inventory
- Total pre-seeded permissions: **375 permissions** across **38 modules**.
- Standard module actions: 10 permissions per module (`approve`, `assign`, `create`, `delete`, `download`, `edit`, `export`, `manage`, `upload`, `view`).

### Status of Remaining Modules in Catalog

| Remaining Module | Module in Catalog? | Existing Permissions in DB | New Permissions / Migrations Needed? |
|:---|:---:|:---|:---:|
| `project.py` (DSR, Issues, Work Progress, Tasks) | **YES** | `projects.*`, `dsr.*`, `issues.*`, `work_progress.*`, `tasks.*` (10 per module) | **ZERO** (100% available) |
| `payroll.py` | **YES** | `payroll.view`, `create`, `edit`, `delete`, `approve`, `export`, `manage`, etc. | **ZERO** (100% available) |
| `agreement.py` | **YES** | `agreements.view`, `create`, `edit`, `delete`, `approve`, `export`, etc. | **ZERO** (100% available) |
| `reports.py` | **YES** | `reports.view`, `create`, `edit`, `delete`, `export`, `manage`, etc. | **ZERO** (100% available) |
| `chat.py` | **YES** | `chat.view`, `create`, `edit`, `delete`, `upload`, `download`, etc. | **ZERO** (100% available) |
| `user.py` / `rbac.py` | **YES** | `users.*`, `roles.*`, `permissions.*` (10 per module) | **ZERO** (100% available) |
| `notification.py` / `alert.py` / `settings.py` | **YES** | `notifications.*`, `alerts.*`, `settings.*` (10 per module) | **ZERO** (100% available) |
| `vendor_bills.py` | **PARTIAL** | Can map to existing `billing.*` or `invoices.*` | **ZERO** (catalog reuse recommended) |
| `work_order.py` | **PARTIAL** | Can map to existing `quotations.*` or `contracts.*` / `billing.*` | **Optional** (`work_orders.*` can be added later) |
| `accountant.py` / `journal.py` | **NO** | No `accountant` or `journal` module in catalog (has `billing`, `invoices`, `expenses`) | Needs future migration when scheduled |
| `approval.py` | **NO** | No dedicated `approvals` module; actions use `<module>.approve` | Needs architectural alignment |
| `saas_billing.py` / `superadmin.py` | **N/A** | Platform / tenant admin infrastructure (not tenant-level RBAC) | None |

> [!NOTE]
> All remaining modules recommended for immediate Phase 2 batches (`payroll`, `vendor_bills`, `project` sub-routers, `agreement`) have **100% pre-existing catalog coverage**. Zero database migrations or permission inserts are required to proceed.

## 9. Exact Calculation

### Baseline Reported Calculation:
```
TOTAL = 774
COMPLETED = 341
REMAINING = 433
COVERAGE = 44.06%
```

### Verified Route Inventory Calculation:
```
TOTAL = 774
COMPLETED = 342
REMAINING = 432
COVERAGE = 44.19%
```

### Discrepancy Reconciliation:
- **Reported Baseline**: 341 routes across Batches A–O.
- **Verified Baseline**: 342 active routes across Batches A–O.
- **Discrepancy**: +1 route in actual implementation.
- **Root Cause**: An off-by-one undercount error occurred prior to Batch M (Batches A–L contained 316 active routes with `require_permission`, but was tracked as 315 in `RBAC_PHASE2_BATCH_M_IMPLEMENTATION_REPORT.md`). Batches M (+8), N (+6), and O (+12) incremented accurately but preserved the 1-route baseline deficit:
  - Batch L: 316 verified (reported 315)
  - Batch M: 324 verified (reported 323)
  - Batch N: 330 verified (reported 329)
  - Batch O: 342 verified (reported 341)

## 10. Recommended Batch P

### Recommendation: **Payroll Management (`app/api/payroll.py`)**

**Evaluation Matrix of Prime Candidates:**

| Candidate Module | Routes | Security Risk | Catalog Availability | Sizing & Coupling | Overall Priority |
|:---|:---:|:---|:---:|:---|:---:|
| **Payroll Management (`payroll.py`)** | **11** | **CRITICAL (P1)**: Complete absence of RBAC; any employee can view/process all salaries | **100% PRE-EXISTING** (`payroll.*` pre-seeded) | Sized ideally (11 routes); self-contained | **TOP RECOMMENDATION** |
| **Vendor Bills (`vendor_bills.py`)** | 6 | **HIGH (P1)**: Hardcoded roles; un-isolated billing records | **100% MAPPABLE** (`billing.*` / `invoices.*`) | Sized ideally (6 routes); clean boundary | **STRONG RUNNER-UP** |
| **Central Approvals (`approval.py`)** | 4 | **CRITICAL (P0)**: 10 cross-tenant IDOR points across 9 entities | **ACTION-BASED** (maps to `<mod>.approve`) | Highly coupled to 9 foreign models | **SECONDARY** |
| **Journal Management (`journal.py`)** | 14 | **CRITICAL (P0)**: 9 unauthenticated routes | **MISSING** (requires catalog seeding) | Sized well, but requires DB migration | **DEFERRED (NEEDS CATALOG)** |
| **Agreements (`agreement.py`)** | 4 | **CRITICAL (P0)**: Global cross-tenant disclosure | **100% PRE-EXISTING** (`agreements.*`) | Ultra-compact (4 routes) | **QUICK WIN** |

### Why Payroll Management is the Optimal Batch P:
1. **Critical Vulnerability Remediation**: All 11 routes currently rely on raw `get_current_active_user`. Site engineers, labourers, and general workers can view all company staff salaries, download pay slips, and trigger payroll calculations.
2. **Zero Schema or Database Impact**: Module `'payroll'` is already 100% seeded in the database permission catalog with 10 granular permissions (`payroll.view`, `payroll.create`, `payroll.edit`, `payroll.delete`, `payroll.approve`, `payroll.export`, `payroll.manage`). Zero migrations and zero schema changes needed.
3. **Cohesive Batch Sizing**: Exactly 11 routes, matching the proven batch size of Batch L (13 routes) and Batch O (12 routes).
4. **Architectural Decoupling**: Standalone router with prefix `/accountant/payroll` that integrates cleanly into existing Labour and Attendance modules.

## 11. Final Verdict

- RBAC COMPLETION: 44.06% (Verified: 44.19%)
- ROUTES COMPLETED: 341 (Verified: 342)
- ROUTES REMAINING: 433 (Verified: 432)
- TOTAL PRODUCTION ROUTES: 774
