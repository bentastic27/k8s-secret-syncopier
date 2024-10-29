An application to sync secrets from one namespace to any number of others

Install with:

```
kubectl apply -f install
```

Example:

```
apiVersion: "beansnet.net/v1"
kind: SecretSync
metadata:
  name: some-name
  namespace: some-namespace
spec:
  sourceSecret: some-secret
  destinationNamespaces:
  - some-other-namespace
  - yet-another-namespace
```
