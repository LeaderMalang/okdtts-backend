from datetime import date, datetime

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework.response import Response
from voucher.models import VoucherEntry
from .ratios import current_ratio, gross_profit_margin

from .financial_statements import account_type_balances


@require_http_methods(["GET"])
def report_dashboard(request):

    return render(request, 'report/dashboard.html')


@api_view(["GET"])
def financial_ratios(request):
    entries = VoucherEntry.objects.all()
    data = {
        "currentRatio": current_ratio(entries=entries),
        "grossProfitMargin": gross_profit_margin(entries=entries),
    }
    return Response(data)



@require_http_methods(["GET"])
def financial_statement(request):
    """Return aggregated voucher entry totals grouped by account type.

    The period can be specified via ``start``/``end`` query parameters (ISO
    formatted) or a ``year`` parameter representing a financial year.
    """

    year = request.GET.get("year")
    start = request.GET.get("start")
    end = request.GET.get("end")

    if year:
        start_date = date(int(year), 1, 1)
        end_date = date(int(year), 12, 31)
    elif start and end:
        start_date = datetime.fromisoformat(start).date()
        end_date = datetime.fromisoformat(end).date()
    else:
        return JsonResponse(
            {"detail": "Provide 'year' or both 'start' and 'end' parameters."},
            status=400,
        )

    totals = account_type_balances(start_date=start_date, end_date=end_date)
    # Convert Decimals to strings for JSON serialisation
    data = {k: str(v) for k, v in totals.items()}
    return JsonResponse(data)

