import io
from datetime import timedelta
from django.utils import timezone
from django.http import FileResponse,JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods,require_GET
from .auth_api import body_or_error
from .character_api import authentication_error
from .models import PersonalBackupJob
from .personal_backup import LIMIT


def payload(job):
    return {'id':str(job.pk),'kind':job.kind,'status':job.status,'result':job.result,'error':job.error,'expires_at':(job.created_at+timedelta(days=7)).isoformat(),'created_at':job.created_at.isoformat()}


@require_http_methods(['GET','POST'])
def jobs(request):
    if error:=authentication_error(request):
        return error
    PersonalBackupJob.objects.filter(owner=request.user,created_at__lt=timezone.now()-timedelta(days=7)).exclude(status='running').delete()
    if request.method=='GET':
        return JsonResponse({'jobs':[payload(job) for job in PersonalBackupJob.objects.filter(owner=request.user).order_by('-created_at')[:100]]})
    if request.FILES:
        if set(request.FILES)!={'file'} or request.POST:
            return JsonResponse({'error':'请上传一个个人备份 ZIP 文件'},status=400)
        upload=request.FILES['file']
        if upload.size>LIMIT:
            return JsonResponse({'error':'个人备份文件不能超过 100 MiB'},status=400)
        package=upload.read(LIMIT+1)
        if len(package)>LIMIT:
            return JsonResponse({'error':'个人备份文件不能超过 100 MiB'},status=400)
        job=PersonalBackupJob.objects.create(owner=request.user,kind='preview',package=package)
    else:
        data,error=body_or_error(request)
        if error:
            return error
        if data=={'action':'export'}:
            job=PersonalBackupJob.objects.create(owner=request.user,kind='export')
        elif set(data)=={'action','preview_id'} and data['action']=='restore':
            preview=get_object_or_404(PersonalBackupJob,pk=data['preview_id'],owner=request.user,kind='preview',status='done',created_at__gte=timezone.now()-timedelta(days=7))
            job=PersonalBackupJob.objects.create(owner=request.user,kind='restore',package=preview.package,result={'preview_id':str(preview.pk)})
        else:
            return JsonResponse({'error':'备份任务请求无效'},status=400)
    return JsonResponse(payload(job),status=202)


@require_http_methods(['GET','DELETE'])
def job_detail(request,job_id):
    if error:=authentication_error(request):
        return error
    job=get_object_or_404(PersonalBackupJob,pk=job_id,owner=request.user,created_at__gte=timezone.now()-timedelta(days=7))
    if request.method=='DELETE':
        if job.status=='running':
            return JsonResponse({'error':'后台处理中，请完成后删除'},status=409)
        PersonalBackupJob.objects.filter(pk=job.pk).exclude(status='running').delete()
        return JsonResponse({},status=204)
    return JsonResponse(payload(job))


@require_GET
def download(request,job_id):
    if error:=authentication_error(request):
        return error
    job=get_object_or_404(PersonalBackupJob,pk=job_id,owner=request.user,kind='export',status='done',created_at__gte=timezone.now()-timedelta(days=7))
    return FileResponse(io.BytesIO(bytes(job.package)),as_attachment=True,filename='霓虹酒馆个人备份.zip',content_type='application/zip')
