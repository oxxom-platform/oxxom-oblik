from django.urls import path
from demo_booking import views as demo_booking_views
handler400 = 'core.errors.bad_request'
handler403 = 'core.errors.permission_denied'
handler404 = 'core.errors.not_found'
handler500 = 'core.errors.server_error'
from core.views import home, health, ready, brand_logo, brand_logo_svg, workspace_asset, landing_asset, ui_asset, robots, sitemap, public_brand_asset
from core import workspace as workspace_views
from accounts import auth_views
from accounts import member_views
from catalog import views as catalog_views
from documents import views as document_views
from inventory import views as inventory_views
from posting import views as posting_views
from operations import views as operation_views
urlpatterns = [path("assets/brand/oxxom-oblik.svg", brand_logo_svg), path("assets/brand/oxxom-oblik.png", brand_logo), path("", home), path("api/v1/health", health), path("api/v1/ready", ready)]
urlpatterns += [path('demo/', demo_booking_views.page),
                path('en/demo/', demo_booking_views.page, {'language': 'en'}),
                path('ru/demo/', demo_booking_views.page, {'language': 'ru'}),
                path('cs/demo/', demo_booking_views.page, {'language': 'cs'}),
                path('api/public/demo-requests', demo_booking_views.submit)]
urlpatterns += [path('en/', home, {'language': 'en'}), path('ru/', home, {'language': 'ru'}),
                path('favicon.ico', public_brand_asset, {'filename': 'favicon.ico'}),
                path('cs/', home, {'language': 'cs'}), path('robots.txt', robots), path('sitemap.xml', sitemap),
                path('assets/brand/<str:filename>', public_brand_asset)]
urlpatterns += [
    path("app/", workspace_views.page),
    path("team/", member_views.workspace),
    path("assets/workspace/<str:filename>", workspace_asset),
    path("assets/landing/<str:filename>", landing_asset),
    path("assets/ui/<str:filename>", ui_asset),
    path("api/v1/organizations/<uuid:organization_id>/workspace-overview", workspace_views.overview),
    path("api/v1/organizations/<uuid:organization_id>/members", member_views.members),
    path("api/v1/organizations/<uuid:organization_id>/members/add", member_views.add_member),
    path("api/v1/organizations/<uuid:organization_id>/members/<uuid:membership_id>", member_views.change_member),
    path("api/v1/organizations/<uuid:organization_id>/access-events", member_views.access_events),
    path("login/", auth_views.auth_page),
    path("assets/auth/login.js", auth_views.login_script),
    path("api/v1/auth/login", auth_views.password_login),
    path("api/v1/auth/setup", auth_views.setup),
    path("api/v1/auth/verify", auth_views.verify),
    path("api/v1/auth/logout", auth_views.sign_out),
    path("api/v1/auth/recovery/regenerate", auth_views.regenerate_recovery),
    path("api/v1/auth/sessions/revoke-others", auth_views.terminate_other_sessions),
    path("api/v1/organizations", auth_views.organizations),
]

urlpatterns += [
    path("catalog/", catalog_views.page),
    path("api/v1/organizations/<uuid:organization_id>/catalog-permissions", catalog_views.permissions),
    path("api/v1/organizations/<uuid:organization_id>/catalog/<str:kind>", catalog_views.records),
    path("api/v1/organizations/<uuid:organization_id>/catalog/<str:kind>/add", catalog_views.add),
    path("api/v1/organizations/<uuid:organization_id>/catalog/<str:kind>/<uuid:record_id>", catalog_views.update),
    path("api/v1/organizations/<uuid:organization_id>/catalog/<str:kind>/<uuid:record_id>/state", catalog_views.state),
]

urlpatterns += [
    path("documents/", document_views.page),
    path("api/v1/organizations/<uuid:organization_id>/document-permissions", document_views.permissions),
    path("api/v1/organizations/<uuid:organization_id>/documents", document_views.listing),
    path("api/v1/organizations/<uuid:organization_id>/documents/add", document_views.add),
    path("api/v1/organizations/<uuid:organization_id>/documents/<uuid:document_id>", document_views.detail),
    path("api/v1/organizations/<uuid:organization_id>/documents/<uuid:document_id>/save", document_views.update),
    path("api/v1/organizations/<uuid:organization_id>/documents/<uuid:document_id>/state", document_views.state),
    path("api/v1/organizations/<uuid:organization_id>/documents/<uuid:document_id>/copy", document_views.copy),
]

urlpatterns += [
    path("inventory/", inventory_views.page),
    path("api/v1/organizations/<uuid:organization_id>/inventory-permissions", inventory_views.permissions),
    path("api/v1/organizations/<uuid:organization_id>/inventory", inventory_views.listing),
    path("api/v1/organizations/<uuid:organization_id>/inventory/add", inventory_views.add),
    path("api/v1/organizations/<uuid:organization_id>/inventory/<uuid:document_id>", inventory_views.detail),
    path("api/v1/organizations/<uuid:organization_id>/inventory/<uuid:document_id>/save", inventory_views.update),
    path("api/v1/organizations/<uuid:organization_id>/inventory/<uuid:document_id>/state", inventory_views.state),
]

urlpatterns += [
    path("stock/", posting_views.page),
    path("api/v1/organizations/<uuid:organization_id>/balances", posting_views.balances),
    path("api/v1/organizations/<uuid:organization_id>/movements", posting_views.movements),
    path("api/v1/organizations/<uuid:organization_id>/period", posting_views.period),
    path("api/v1/organizations/<uuid:organization_id>/period/save", posting_views.change_period),
    path("api/v1/organizations/<uuid:organization_id>/documents/<uuid:document_id>/post", posting_views.post_sale),
    path("api/v1/organizations/<uuid:organization_id>/documents/<uuid:document_id>/reverse", posting_views.reverse_sale),
    path("api/v1/organizations/<uuid:organization_id>/inventory/<uuid:document_id>/post", posting_views.post_stock),
    path("api/v1/organizations/<uuid:organization_id>/inventory/<uuid:document_id>/reverse", posting_views.reverse_stock),
]

urlpatterns += [
 path("operations/",operation_views.page),
 path("api/v1/organizations/<uuid:organization_id>/operation-permissions",operation_views.permissions),
 path("api/v1/organizations/<uuid:organization_id>/operations",operation_views.listing),
 path("api/v1/organizations/<uuid:organization_id>/operations/add",operation_views.add),
 path("api/v1/organizations/<uuid:organization_id>/operations/<uuid:document_id>",operation_views.detail),
 path("api/v1/organizations/<uuid:organization_id>/operations/<uuid:document_id>/save",operation_views.update),
 path("api/v1/organizations/<uuid:organization_id>/operations/<uuid:document_id>/state",operation_views.state),
 path("api/v1/organizations/<uuid:organization_id>/operations/<uuid:document_id>/post",posting_views.post_operation),
 path("api/v1/organizations/<uuid:organization_id>/operations/<uuid:document_id>/reverse",posting_views.reverse_operation),
 path("api/v1/organizations/<uuid:organization_id>/return-sources",operation_views.return_sources),
 path("api/v1/organizations/<uuid:organization_id>/return-sources/<uuid:sale_id>",operation_views.return_lines),
]

urlpatterns += [
 path("api/v1/organizations/<uuid:organization_id>/documents/save-and-post",posting_views.save_post_sale),
 path("api/v1/organizations/<uuid:organization_id>/documents/<uuid:document_id>/save-and-post",posting_views.save_post_sale),
 path("api/v1/organizations/<uuid:organization_id>/inventory/save-and-post",posting_views.save_post_stock),
 path("api/v1/organizations/<uuid:organization_id>/inventory/<uuid:document_id>/save-and-post",posting_views.save_post_stock),
 path("api/v1/organizations/<uuid:organization_id>/operations/save-and-post",posting_views.save_post_operation),
 path("api/v1/organizations/<uuid:organization_id>/operations/<uuid:document_id>/save-and-post",posting_views.save_post_operation),
]

from payments import views as payment_views
urlpatterns += [
 path("payments/",payment_views.page),
 path("api/v1/organizations/<uuid:organization_id>/payment-permissions",payment_views.permissions),
 path("api/v1/organizations/<uuid:organization_id>/money-accounts",payment_views.accounts),
 path("api/v1/organizations/<uuid:organization_id>/money-accounts/add",payment_views.save_account),
 path("api/v1/organizations/<uuid:organization_id>/money-accounts/<uuid:account_id>/save",payment_views.save_account),
 path("api/v1/organizations/<uuid:organization_id>/payments",payment_views.listing),
 path("api/v1/organizations/<uuid:organization_id>/payments/add",payment_views.add),
 path("api/v1/organizations/<uuid:organization_id>/payments/save-and-post",payment_views.save_post),
 path("api/v1/organizations/<uuid:organization_id>/payments/<uuid:payment_id>",payment_views.detail),
 path("api/v1/organizations/<uuid:organization_id>/payments/<uuid:payment_id>/save",payment_views.update),
 path("api/v1/organizations/<uuid:organization_id>/payments/<uuid:payment_id>/save-and-post",payment_views.save_post),
 path("api/v1/organizations/<uuid:organization_id>/payments/<uuid:payment_id>/post",payment_views.post),
 path("api/v1/organizations/<uuid:organization_id>/payments/<uuid:payment_id>/reverse",payment_views.reverse),
 path("api/v1/organizations/<uuid:organization_id>/payments/<uuid:payment_id>/allocate",payment_views.allocate),
 path("api/v1/organizations/<uuid:organization_id>/payments/<uuid:payment_id>/state",payment_views.state),
 path("api/v1/organizations/<uuid:organization_id>/payment-documents",payment_views.documents),
 path("api/v1/organizations/<uuid:organization_id>/debts",payment_views.debts),
 path("api/v1/organizations/<uuid:organization_id>/money-movements",payment_views.journal),
 path("api/v1/organizations/<uuid:organization_id>/payment-events",payment_views.audit),
]

from reports import views as report_views
urlpatterns += [
 path("reports/",report_views.page),
 path("api/v1/organizations/<uuid:organization_id>/report-options",report_views.options),
 path("api/v1/organizations/<uuid:organization_id>/reports/<str:kind>",report_views.data),
 path("api/v1/organizations/<uuid:organization_id>/reports/<str:kind>/export",report_views.export),
]

from printing import views as print_views
urlpatterns += [
 path("organization-details/",print_views.page),
 path("api/v1/organizations/<uuid:organization_id>/issuer-profile",print_views.profile),
 path("api/v1/organizations/<uuid:organization_id>/issuer-profile/save",print_views.update_profile),
 path("api/v1/organizations/<uuid:organization_id>/issuer-events",print_views.profile_events),
 path("print/<uuid:organization_id>/<uuid:document_id>/<str:kind>/",print_views.preview),
]

from catalog_imports import views as import_views
urlpatterns += [
 path("imports/",import_views.page),
 path("api/v1/organizations/<uuid:organization_id>/import-options",import_views.options),
 path("api/v1/organizations/<uuid:organization_id>/import-templates/<str:kind>",import_views.template),
 path("api/v1/organizations/<uuid:organization_id>/imports",import_views.listing),
 path("api/v1/organizations/<uuid:organization_id>/imports/<str:kind>/preview",import_views.preview),
 path("api/v1/organizations/<uuid:organization_id>/imports/<uuid:batch_id>",import_views.detail),
 path("api/v1/organizations/<uuid:organization_id>/imports/<uuid:batch_id>/confirm",import_views.apply),
 path("api/v1/organizations/<uuid:organization_id>/imports/<uuid:batch_id>/refresh",import_views.recheck),
]
