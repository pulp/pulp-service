# RUNNING FROM SOURCE

* installing dependencies
```
sudo dnf install -y matplotlib
pip install requests
```


```
mkdir /tmp/tasks-cli
```

* running

    ```
    cd management_tools
    ```

    * stage
    ```
    HTTPS_PROXY=http://<internal proxy address> python tasks-cli.py -c <cert file> -k <cert key> --base_address https://mtls.internal.console.stage.redhat.com
    ```

    * prod
    ```
    python tasks-cli.py -c <cert file> -k <cert key> --base_address https://mtls.internal.console.redhat.com
    ```


# RUNNING AS A CONTAINER

* building
```
cd management_tools/
podman build -t tasks-cli:latest .
```

* running
    * ephemeral
    ```
    podman run -v /tmp/tasks-cli:/tmp/tasks-cli -it --rm tasks-cli python tasks-cli.py \
      --base_address http://$(oc get routes pulp-api -ojsonpath='{.spec.host}') \
      --password $(oc extract secret/pulp-admin-password --to=-)

    ```

    * stage
    ```
    podman run -v <path with the cert/key files>:/tmp/cert-dir/ -v /tmp/tasks-cli:/tmp/tasks-cli -it --rm tasks-cli HTTPS_PROXY=<internal proxy address> python tasks-cli.py \
    --period 2 --bucket_size 600 \
    --base_address https://mtls.internal.console.stage.redhat.com \
    -c /tmp/cert-dir/<cert file> \
    -k /tmp/cert-dir/<cert key> \
    --output /tmp/tasks-cli/<metrics file> \
    -g /tmp/tasks-cli/<graph file>
    ```

    * prod
    ```
    podman run -v <path with the cert/key files>:/tmp/cert-dir/ -v /tmp/tasks-cli:/tmp/tasks-cli -it --rm tasks-cli python tasks-cli.py \
    --period 2 --bucket_size 600 \
    --base_address https://mtls.internal.console.redhat.com \
    -c /tmp/cert-dir/<cert file> \
    -k /tmp/cert-dir/<cert key> \
    --output /tmp/tasks-cli/<metrics file> \
    -g /tmp/tasks-cli/<graph file>
    ```
