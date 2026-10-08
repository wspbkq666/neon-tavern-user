from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods
from .auth_api import body_or_error
from .character_api import authentication_error
from .models import ImportBatch
from .import_history import preview_import_undo,undo_import_batch


@require_http_methods(['GET'])
def batches(request):
    if error:=authentication_error(request):
        return error
    rows=ImportBatch.objects.filter(owner=request.user).select_related('task').order_by('-created_at')[:100]
    return JsonResponse({'batches':[{'id':str(row.pk),'filename':row.task.filename if row.task else '整合素材导入','created_at':row.created_at.isoformat(),'count':len(row.created_objects),'undone':bool(row.undo_result),'result':row.result} for row in rows]})


@require_http_methods(['GET','POST'])
def batch_detail(request,batch_id):
    if error:=authentication_error(request):
        return error
    get_object_or_404(ImportBatch,pk=batch_id,owner=request.user)
    if request.method=='GET':
        return JsonResponse(preview_import_undo(request.user,batch_id))
    data,error=body_or_error(request)
    if error:
        return error
    if set(data)!={'revision'} or type(data['revision']) is not int:
        return JsonResponse({'error':'撤销请求无效'},status=400)
    try:
        result=undo_import_batch(request.user,batch_id,data['revision'])
    except ValueError as exc:
        return JsonResponse({'error':str(exc)},status=409)
    return JsonResponse(result)
