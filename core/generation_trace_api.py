from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
from .character_api import authentication_error
from .models import Conversation,GenerationContextTrace
from .generation_trace import public_context_trace


@require_GET
def traces(request,conversation_id):
    if error:=authentication_error(request):
        return error
    story=get_object_or_404(Conversation,pk=conversation_id,owner=request.user)
    rows=GenerationContextTrace.objects.filter(job__conversation=story,job__requested_by=request.user).select_related('job').order_by('-created_at')
    if identifier:=request.GET.get('trace_id'):
        from uuid import UUID
        try: identifier=UUID(identifier)
        except ValueError: return JsonResponse({'error':'设定记录编号无效'},status=400)
        rows=rows.filter(pk=identifier)
    rows=rows[:100]
    return JsonResponse({'traces':[public_context_trace(row) for row in rows]},json_dumps_params={'ensure_ascii':False})
