from pulpcore.plugin.tasking import dispatch

from pulp_python.app.models import PythonRemote, PythonRepository


def python_repository_sync():
    """Dispatch a task to sync Lightwell python repository with Trusted Libraries remote"""

    domain_name = "lightwell"
    repository_name = "network-python-validated-landing"
    remote_name = "trusted-libraries"

    try:
        repository = PythonRepository.objects.get(name=repository_name, pulp_domain__name=domain_name)
    except PythonRepository.DoesNotExist:
        # we don't have this repository and domain configured in staging, so if the repository
        # does not exist we will just skip the task
        return

    remote_pk = PythonRemote.objects.values_list("pk", flat=True).get(name=remote_name, pulp_domain__name=domain_name)
    dispatch(
        "pulp_python.app.tasks.sync.sync",
        exclusive_resources=[repository],
        kwargs={
            "remote_pk": str(remote_pk),
            "repository_pk": str(repository.pk),
        },
    )
