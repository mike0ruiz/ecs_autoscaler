#!/usr/bin/env python

# boto3 is in developer preview: live dangerously and luxuriate in the sensible syntax
import boto3
import logging
import argparse

# Set the desired number of totally free instances (not running tasks) in the cluster to this value
MIN_INSTANCES_FREE = 0
DEFAULT_CLUSTER = "default"

#Set up command line arg parser

def get_args():

    parser = argparse.ArgumentParser(description= 'Define Cluster and your minimum instances free in your cluster')
    parser.add_argument('-c', '--cluster', type=str, help='Your ECS cluster name', nargs=1, required=True)
    parser.add_argument('-f', '--free-instances', type=int, help="Minimum desired count of free instances",  nargs=1, required=True)
    return vars(parser.parse_args())


    cluster = args.cluster
    free_instances = args.free_instances
    return cluster, free_instances 


# setup paranoid logging for this module
logging.basicConfig(level=logging.WARNING)
logging.getLogger(__name__).setLevel(logging.DEBUG)
log = logging.getLogger(__name__)

# Take an instance or list of instances and return the first autoscaling group found
def find_instance_asg(instances=None):

    if instances is None:
        return []

    autoscaling = boto3.client('autoscaling')
    asg_instances = autoscaling.describe_auto_scaling_instances(InstanceIds=[instances])
    return asg_instances["AutoScalingInstances"][0]["AutoScalingGroupName"]


# Take a cluster name and return a list of instances that have no current or pending tasks
def find_free_instances(cluster=None, instance_arns=None):

    if instance_arns is None:
        return []

    ecs = boto3.client('ecs')
    free_instances = []

    response = ecs.describe_container_instances(containerInstances=instance_arns)

    for instance in response["containerInstances"]:
        if instance["runningTasksCount"] == 0 and instance["pendingTasksCount"] == 0:
            free_instances.append(instance["ec2InstanceId"])

    return free_instances


# Return a list of dicts of all cluster instance attributes specified by list 'instance_attributes'
# By default this just returns a list of dicts of all cluster instance instanceIds
def find_cluster_instances(cluster=None, instance_attributes=['ec2InstanceId']):

    ecs = boto3.client('ecs')
    instance_list = []

    response = ecs.list_container_instances(cluster=cluster)["containerInstanceArns"]
    instances = ecs.describe_container_instances(containerInstances=response)["containerInstances"]

    for instance in instances:
        attribute = {}
        for key in instance_attributes:
            attribute[key] = instance[key]
        instance_list.append(attribute)

    return instance_list


# Take a cluster name and return a dict containing the pending and desired number of
# tasks for each service
def find_service_task_count(cluster=None):

    task_list = []

    ecs = boto3.client('ecs')

    service_arns = ecs.list_services(cluster=cluster)['serviceArns']
    services = ecs.describe_services(services=service_arns, cluster=cluster)['services']

    for service in services:
        task = {}
        for key in ['serviceName', 'desiredCount', 'runningCount', 'pendingCount']:
            task[key] = service[key]
        task_list.append(task)

    return task_list


def update_asg_count(asg=None, count=0, instanceId=None):

    if asg is None or count is 0:
        return []

    if count < 0 and instanceId is None:
        log.error ("Can't scale-in without instanceId to terminate")
        return []

    autoscaling = boto3.client('autoscaling')

    response = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg])

    asg_desired_capacity = response['AutoScalingGroups'][0]['DesiredCapacity'] + count

    if asg_desired_capacity > response['AutoScalingGroups'][0]['MaxSize']:
        log.error ("Can't increase desired capacity beyond ASG MaxSize (%s)\n\tfor ASG: %s",
                       response['AutoScalingGroups'][0]['MaxSize'], asg)
        log.error ("Perhaps increase MaxSize and try again?")

    elif asg_desired_capacity < response['AutoScalingGroups'][0]['MinSize']:
        log.error ("Can't decrease desired capacity to lower than ASG MinSize (%s)\n\t for ASG: %s",
                   response['AutoScalingGroups'][0]['MinSize'], asg)
        log.error ("Perhaps decrease MinSize and try again?")

    elif response['AutoScalingGroups'][0]['DesiredCapacity'] != len(response['AutoScalingGroups'][0]['Instances']):
        log.error ("Won't adjust size while DesiredCapacity (%s) does not match instance count (%s)\n\t for ASG %s",
                   response['AutoScalingGroups'][0]['DesiredCapacity'],
                   len(response['AutoScalingGroups'][0]['Instances']), asg)

    # Scale out ASG
    elif count > 0:
        autoscaling.set_desired_capacity(AutoScalingGroupName=asg,
                                         DesiredCapacity=asg_desired_capacity)
    # Scale in ASG
    elif count < 0:
        autoscaling.terminate_instance_in_auto_scaling_group(InstanceId=instanceId,
                                                             ShouldDecrementDesiredCapacity=True)

    return []

update_asg = 0


# Build a list of cluster instance ARNs and InstanceIds
cluster_instances = find_cluster_instances(DEFAULT_CLUSTER, ["containerInstanceArn", "ec2InstanceId"])

# Grab list of instances that have no running or pending tasks
free_instance_list = find_free_instances(DEFAULT_CLUSTER, [elem['containerInstanceArn'] for elem in cluster_instances])

log.debug ("Cluster Free Instances:  %s, %s", len(free_instance_list), free_instance_list)
log.debug ("Cluster Total Instances: %s", len([elem['containerInstanceArn'] for elem in cluster_instances]))


# Make a single scaling decision for this run...

# If instance free count is less than desired free count, add one instance to autoscaling group
if len(free_instance_list) < MIN_INSTANCES_FREE:
    log.debug ("Cluster Free instance list (%s) is lower than min free instances (%s), trying to add an instance...",
               len(free_instance_list), MIN_INSTANCES_FREE)
    update_asg = 1

# If there are no free instances and desired tasks is less than running tasks, add one to autoscaling group
elif len(free_instance_list) == 0:
    log.debug ("No free instances, checking service task counts...")
    for service in find_service_task_count(DEFAULT_CLUSTER):
        log.debug (" Service \"%s\"", service['serviceName'])
        log.debug ("    Running Tasks: %s", service['runningCount'])
        log.debug ("    Desired Tasks: %s", service['desiredCount'])
        log.debug ("    Pending Tasks: %s", service['pendingCount'])
        if service['runningCount'] < service['desiredCount'] and service['pendingCount'] == 0:
            log.debug ("desired tasks are less than running tasks with no tasks pending."
                       "Trying to add an instance...")
            update_asg = 1
        else:
            log.debug ("desired tasks counts seem fine.")

# If we got here, we have may have free instances we don't need: reduce the count
elif len(free_instance_list) > MIN_INSTANCES_FREE:
    log.debug ("Free instance list (", len(free_instance_list), ") is higher than min free instances (",
                   MIN_INSTANCES_FREE, "). Trying to remove an instance...")
    update_asg = -1


# If we decided to update the autoscaling group instance count, do so here
if update_asg != 0:
    cluster_instance_asg = find_instance_asg(cluster_instances[0]['ec2InstanceId'])
    log.debug ("Scaling requested. Desired count will be adjusted by", update_asg, "\nASG:", cluster_instance_asg)
    if cluster_instance_asg and update_asg > 0:
        update_asg_count(cluster_instance_asg, update_asg)
    elif cluster_instance_asg and update_asg < 0 and len(free_instance_list):
        update_asg_count(cluster_instance_asg, update_asg, free_instance_list[0])
