# ecs_autoscaler
Tool for adding autoscaling support to an AWS EC2 Container Service cluster. The tool will scale-in or scale-out the EC2 Auto Scaling group based on instance resource shortfalls. As we don't wish to mirror the task placement logic of the ECS secheduler, we are going to make some assumptions about what we consider actionable resource shortfalls.

## ECS Description
An ECS cluster is comprised of instances, services, and tasks:

### Instances
An ECS cluster contains some number of EC2 instances. Instances are added to (and removed from) the cluster by the operation of an [EC2 Auto Scaling Group](http://docs.aws.amazon.com/AutoScaling/latest/DeveloperGuide/WhatIsAutoScaling.html)(ASG). The ASG has an associated 'launch configuration' that contains logic to add servers to an ECS cluster on scale-up, and remove servers on scale-in. Our scaling logic in this script simply adjusts the ASG 'desired instance count' for the group -- while respecting the user-defined settings for minimum and maximum group size.

### Tasks
An ECS cluster contains one or more user-defined [Task Definition](http://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_defintions.html). Tasks specify all of the details of the job, including instance resource requirements (CPU, Memory, Ports). If the resource requirements are not met, ECS will be unable to schedule the task.

### Services
An ECS cluster can schedule tasks as a [Service](http://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs_services.html). ECS services can specify that one or more instances of a task be scheduled on the cluster. Desired task counts can be modified over the life of the service: it's this feature that allows the service to be automatically scaled as load changes. It's possible to increment the task count beyond the available compute resources in the cluster: it's the job of this script to adjust the instance count to accomidate the reqested service task count by looking for a difference in requested versus running tasks and adding instances via the ASG when there is a shortfall.

## Scaling logic

The tool will attempt to increment or decrement the instance count for the ASG associated with an ECS cluster based on the following flow:

1. Get a count of the current 'free' instances in the cluster. 'Free' instances are those not currently running a tasks.
1. IF the 'free' count is less than the minimum free, try to increment the ASG desired instance count by one.
1. ELSE check each service for 'desired tasks less than running tasks'. If any service has 'desired tasks less than running tasks', try to increment the ASG desired instance count by one
1. ELSE if the 'free' count is greater than the minimum free, try to decrement the ASG desired instance count by one.

To prevent trashing, the increment/decrement be skipped if ASG 'desired count' does not match the 'active count'. The increment/decrement logic will also respect the preexisting values for ASG max and min instance counts.
