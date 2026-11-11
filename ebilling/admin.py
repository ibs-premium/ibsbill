from django.contrib import admin
from django.utils.html import format_html

from .models import Application, CallBacks, EBill

admin.site.site_header = 'IBS eBilling administration'
admin.site.site_title = 'IBS eBilling admin'
admin.site.index_title = 'Manage applications and payments'


# ---------- Applications ----------

@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'ip_address', 'bills')
    search_fields = ('name', 'url', 'ip_address')
    ordering = ('name',)

    @admin.display(description='Transactions')
    def bills(self, obj):
        return EBill.objects.filter(app=obj.name).count()


# ---------- Transactions ----------

@admin.register(EBill)
class EBillAdmin(admin.ModelAdmin):
    list_display = ('request_datetime', 'app', 'device_id', 'momo_number', 'amount', 'status_badge',
                    'transaction_complete')
    list_filter = ('status', 'failure_reason', 'transaction_complete', 'app')
    search_fields = ('uuid', 'device_id', 'momo_number', 'momo_name', 'financial_transaction_id', 'username')
    date_hierarchy = 'request_datetime'
    ordering = ('-request_datetime',)
    list_per_page = 50
    save_on_top = True
    readonly_fields = ('uuid', 'request_datetime', 'request_completed_datetime')
    fieldsets = (
        ('Payment', {'fields': ('app', 'device_id', 'amount', 'status', 'failure_reason', 'transaction_complete')}),
        ('Payer', {'fields': ('momo_number', 'momo_name', 'username')}),
        ('MTN', {'fields': ('uuid', 'financial_transaction_id', 'request_sent',
                            'momo_api_initial_status_code', 'momo_api_initial_message')}),
        ('Timing', {'fields': ('request_datetime', 'request_completed_datetime')}),
        ('Technical details', {'fields': ('app_ip', 'app_url'), 'classes': ('collapse',)}),
    )

    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        colour = {'SUCCESSFUL': '#017a3d', 'FAILED': '#be123c', 'PENDING': '#b45309'}.get(obj.status, '#6b7280')
        return format_html('<b style="color:{}">{}</b>', colour, obj.status or '—')


# ---------- Callback logs ----------

@admin.register(CallBacks)
class CallBacksAdmin(admin.ModelAdmin):
    list_display = ('date', 'method', 'url', 'ip_address')
    list_filter = ('method',)
    search_fields = ('url', 'ip_address', 'contents', 'query_params')
    date_hierarchy = 'date'
    ordering = ('-date',)
    list_per_page = 50

    # Logs are a record of what was received, so they are view-only
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
