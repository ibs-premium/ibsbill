"""Registered applications."""
from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..forms import ApplicationForm
from ..models import Application, EBill
from .common import staff_required


@staff_required
def applications(request):
    form = ApplicationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        app = form.save()
        messages.success(request, f'Application "{app.name}" registered.')
        return redirect('dashboard:applications')

    apps = Application.objects.order_by('name')
    stats = {
        row['app']: row
        for row in EBill.objects.values('app').annotate(
            count=Count('id'),
            collected=Sum('amount', filter=Q(status='SUCCESSFUL')),
        )
    }
    for app in apps:
        app.stats = stats.get(app.name, {})

    return render(request, 'dashboard/applications.html', {
        'nav': 'applications',
        'apps': apps,
        'form': form,
    })


@staff_required
def application_edit(request, pk):
    app = get_object_or_404(Application, pk=pk)
    form = ApplicationForm(request.POST or None, instance=app)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Application "{app.name}" updated.')
        return redirect('dashboard:applications')
    return render(request, 'dashboard/application_edit.html', {
        'nav': 'applications',
        'app': app,
        'form': form,
    })


@staff_required
@require_POST
def application_delete(request, pk):
    app = get_object_or_404(Application, pk=pk)
    app.delete()
    messages.success(request, f'Application "{app.name}" removed.')
    return redirect('dashboard:applications')
