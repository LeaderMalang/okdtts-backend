from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from inventory.models import Party
from .models import InvestorTransaction
from .serializers import InvestorTransactionSerializer, PartySerializer


class InvestorTransactionViewSet(viewsets.ModelViewSet):
    queryset = InvestorTransaction.objects.all()
    serializer_class = InvestorTransactionSerializer


class InvestorViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Party.objects.filter(party_type="investor")
    serializer_class = PartySerializer

    @action(detail=True, methods=["get"])
    def ledger(self, request, pk=None):
        investor = self.get_object()
        transactions = InvestorTransaction.objects.filter(
            investor=investor
        ).order_by("date", "id")

        balance = 0
        ledger_entries = []
        for tx in transactions:
            if tx.transaction_type == "IN":
                credit = tx.amount
                debit = 0
                balance += credit
            else:
                credit = 0
                debit = tx.amount
                balance -= debit

            ledger_entries.append(
                {
                    "date": tx.date,
                    "description": tx.description,
                    "debit": debit,
                    "credit": credit,
                    "balance": balance,
                }
            )

        return Response(ledger_entries)
