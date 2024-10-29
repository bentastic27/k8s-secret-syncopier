from kubernetes import client, config, watch


def main():
  config.load_incluster_config()
  v1 = client.CoreV1Api()
  co = client.CustomObjectsApi()
  w = watch.Watch()

  # for testing
  count = 10

  for event in w.stream(co.list_cluster_custom_object, "beansnet.net", "v1", "secretsyncs"):
    print("Event: %s %s" % (event['type'], event['object']))

    # for testing
    count -= 1
    if not count:
        print("bai")
        w.stop()


if __name__ == '__main__':
  main()