from uuid import uuid4

from pulpcore.plugin.models import Distribution


def no_op_task():
    pass

def create_distribution_task(distribution_name):
    Distribution.objects.create(name=distribution_name)
