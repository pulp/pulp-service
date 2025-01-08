FROM registry.fedoraproject.org/fedora-minimal:39

RUN microdnf install -y pip gnuplot &&  microdnf clean all
RUN pip install requests && rm -rf /root/.cache/pip

COPY gnuplot-script .
COPY tasks-cli.py .

CMD ["python", "tasks-cli.py"]
