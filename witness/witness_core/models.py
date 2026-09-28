from django.db import models


class LeaseState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    holder_id = models.CharField(max_length=64, blank=True, default="")
    epoch = models.PositiveBigIntegerField(default=0)
    issued_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    signature = models.CharField(max_length=128, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "witness_lease_state"


class RequestNonce(models.Model):
    node_id = models.CharField(max_length=64)
    request_id = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "witness_request_nonce"
        constraints = [
            models.UniqueConstraint(fields=("node_id", "request_id"), name="unique_witness_node_request"),
        ]
