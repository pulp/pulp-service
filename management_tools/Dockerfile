FROM registry.fedoraproject.org/fedora-minimal:42

RUN microdnf install -y pip  &&  microdnf clean all
RUN pip install requests matplotlib && rm -rf /root/.cache/pip

COPY tasks-cli.py .

CMD ["python", "tasks-cli.py"]
