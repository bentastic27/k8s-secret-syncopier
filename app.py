import threading
from time import sleep

from kubernetes import client, config, watch

try:
  config.load_incluster_config()
except:
  config.load_kube_config()

v1 = client.CoreV1Api()
co = client.CustomObjectsApi()
w = watch.Watch()


def sync_from_secretsyncs(event):
  namespace = event['object']['metadata']['namespace']
  secret_name = event['object']['spec']['sourceSecret']
  if event['type'] != "DELETED":
    try:
      try:
        source_secret = v1.read_namespaced_secret(secret_name, namespace)
      except client.exceptions.ApiException as err:
        if err.status == 404:
          print(f"Source secret {namespace}/{secret_name}, doesn't exist, creating empty secret")
          source_secret = v1.create_namespaced_secret(namespace, body={'metadata': {'name': secret_name}})
        else:
          print(err.body)

      if source_secret.metadata.labels is not None:
        new_labels = source_secret.metadata.labels.copy()
        new_labels['sourced-with'] = 'secret-syncopier'
      else:
        new_labels = {'sourced-with': 'secret-syncopier'}

      if source_secret.metadata.annotations is not None:
        new_annotations = source_secret.metadata.annotations.copy()
        new_annotations['sourced-by'] = f"{namespace}/{event['object']['metadata']['name']}"
      else:
        new_annotations = {'sourced-by': f"{namespace}/{event['object']['metadata']['name']}"}

      v1.patch_namespaced_secret(secret_name, namespace, body={
        'metadata': {
          'labels': new_labels,
          'annotations': new_annotations
        }
      })

      # changing metadata of the "source_secret" to apply to the destinations
      source_secret.metadata = {
        'name': secret_name,
        'labels': {'managed-by': 'secret-syncopier'},
        'annotations': {
          'source-secret': f"{namespace}/{secret_name}",
          'secret-syncopier': f"{namespace}/{event['object']['metadata']['name']}"
        }
      }

      ns_list = []
      for ns in v1.list_namespace().items:
        ns_list.append(ns.metadata.name)

      for destination_namespace in event['object']['spec']['destinationNamespaces']:
        print(f"Syncing {destination_namespace}/{secret_name}")
        if destination_namespace not in ns_list:
          print(f"Namespace {destination_namespace} does not exist, skipping secret {secret_name}")
          continue
        try:
          print(f"Patching {destination_namespace}/{secret_name} secret if it exists")
          v1.patch_namespaced_secret(secret_name, destination_namespace, source_secret)
        except client.exceptions.ApiException as err:
          if err.status == 404:
            print(f"{destination_namespace}/{secret_name} doesn't exist, creating secret")
            v1.create_namespaced_secret(destination_namespace, source_secret)
          else:
            print(err.body)

    except client.exceptions.ApiException as err:
      print(err.body)

  else:
    for destination_namespace in event['object']['spec']['destinationNamespaces']:
      try:
        print(f"Deleting secret {destination_namespace}/{secret_name}")
        v1.delete_namespaced_secret(name=secret_name, namespace=destination_namespace)
      except client.exceptions.ApiException as err:
        print(err.body)
    
    print(f"Cleaning labels/annotations on {namespace}/{secret_name}")
    try:
      source_secret = v1.read_namespaced_secret(secret_name, namespace)
      v1.patch_namespaced_secret(secret_name, namespace, body={
        'metadata': {
          'annotations': {'sourced-by': None},
          'labels': {'sourced-with': None}
        }
      })
    except client.exceptions.ApiException as err:
        print(err.body)


def sync_from_secret_change(event):
  namespace = event['object'].metadata.namespace
  secret_name = event['object'].metadata.name

  if event['type'] != "DELETED" and event['type'] != "ADDED":
    secret_sync_name = event['object'].metadata.annotations['sourced-by'].split("/")[1]
    secret_sync = co.get_namespaced_custom_object("beansnet.net", "v1", namespace, "secretsyncs", secret_sync_name)
    source_secret = v1.read_namespaced_secret(secret_name, namespace)

    ns_list = []
    for ns in v1.list_namespace().items:
      ns_list.append(ns.metadata.name)
    
    for destination_namespace in secret_sync['spec']['destinationNamespaces']:
      if destination_namespace not in ns_list:
        print(f"Namespace {destination_namespace} from {namespace}/{secret_sync_name}'s .spec.destinationNamespaces does not exist, skipping")
        continue

      destination_secret = v1.read_namespaced_secret(secret_name, destination_namespace)
      if source_secret.data != destination_secret.data:
        try:
          v1.patch_namespaced_secret(secret_name, destination_namespace, body={'data': source_secret.data})
        except client.exceptions.ApiException as err:
          print(err.body)


def destination_secret_cleanup(interval=300):
  while True:
    print(f"Starting destination secret cleanup every {str(interval)} seconds")
    for secret in v1.list_secret_for_all_namespaces(label_selector="managed-by=secret-syncopier").items:
      prune_secret = False
      secret_sync_annotation = secret.metadata.annotations['secret-syncopier']

      try:
        secret_sync = co.get_namespaced_custom_object("beansnet.net", "v1", secret_sync_annotation.split("/")[0], "secretsyncs", secret_sync_annotation.split("/")[1])
      except client.exceptions.ApiException as err:
        if err.status == 404:
          secret_sync = None
          prune_secret = True
        else:
          print(err.body)
          continue
      
      if secret_sync is not None:
        if secret.metadata.namespace not in secret_sync['spec']['destinationNamespaces']:
          prune_secret = True
      
      if prune_secret:
        print(f"Pruning secret {secret.metadata.namespace}/{secret.metadata.name}")
        v1.delete_namespaced_secret(secret.metadata.name, secret.metadata.namespace)
      
    sleep(interval)


def stream_secretsyncs():
  for event in w.stream(co.list_cluster_custom_object, "beansnet.net", "v1", "secretsyncs"):
    print("%s %s" % (event['type'], event['object']))
    threading.Thread(target=sync_from_secretsyncs, args=[event]).start()


def stream_secrets():
  for event in w.stream(v1.list_secret_for_all_namespaces, label_selector="sourced-with=secret-syncopier"):
    print("%s %s" % (event['type'], f"{event['object'].metadata.namespace}/{event['object'].metadata.name}"))
    threading.Thread(target=sync_from_secret_change, args=[event]).start()


def main():
  threading.Thread(target=stream_secretsyncs).start()
  threading.Thread(target=stream_secrets).start()
  threading.Thread(target=destination_secret_cleanup).start()

if __name__ == '__main__':
  main()