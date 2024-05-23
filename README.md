# Pulp Clowder Deployments

## Usage example
```
# This creates a ClowdEnvironment object for the namespace. Several services are started.
bonfire namespace reserve --duration 8h
bonfire deploy-env -n $(oc project | grep -oE 'ephemeral-......') --template-file deploy/clowdapp.yaml
```

pulp-clowdenv.yaml is not used and not tested at this time.


