# Invoked by: SNS Subscription
# Returns: Error or status message
#
# Receives notifications from CodePipeline when an execution requires manual
# approval. This will generally be related to a production CloudFormation
# deploy. Information regarding the pending changes to the stack are queried
# from CloudFormation, and sent to Slack along with control to make a decision
# about the deploy.

import boto3
import json
import os

cloudformation = boto3.client('cloudformation')
sns = boto3.client('sns')

CODEPIPELINE_MANUAL_APPROVAL_CALLBACK = 'codepipeline-approval-action'
APPROVED = 'Approved'
REJECTED = 'Rejected'


def parameters_delta_attachment(notification):
    custom_data = json.loads(notification['approval']['customData'])
    stack_name = custom_data['StackName']
    change_set_name = custom_data['ChangeSetName']

    stack = cloudformation.describe_stacks(StackName=stack_name)['Stacks'][0]
    stack_parameters = stack['Parameters']

    change_set = cloudformation.describe_change_set(
        ChangeSetName=change_set_name,
        StackName=stack_name
    )
    change_set_parameters = change_set['Parameters']

    parameters = {}

    for p in stack_parameters:
        if not p['ParameterKey'] in parameters:
            parameters[p['ParameterKey']] = {}

        parameters[p['ParameterKey']]['StackValue'] = p['ParameterValue']

    for p in change_set_parameters:
        if not p['ParameterKey'] in parameters:
            parameters[p['ParameterKey']] = {}

        parameters[p['ParameterKey']]['ChangeSetValue'] = p['ParameterValue']

    deltas = []

    for k, v in parameters.items():
        if 'StackValue' not in v:
            deltas.append(f"*{k}*: ❔ ➡ `{v['ChangeSetValue']}`")

        elif 'ChangeSetValue' not in v:
            deltas.append(f"*{k}*: `{v['StackValue']}` ➡ ❌")

        elif v['StackValue'] != v['ChangeSetValue']:
            before = v['StackValue']
            after = v['ChangeSetValue']
            deltas.append(f"*{k}*: `{before}` ➡ `{after}`")

    unchanged_count = len(parameters) - len(deltas)

    return {
        'title': 'Stack Parameters Delta',
        'footer': f'Excludes {unchanged_count} unchanged parameters',
        'mrkdwn_in': ['text'],
        'text': '\n'.join(deltas)
    }


def approval_action_attachment(notification):
    # All the values the CodePipeline SDK needs to approve or reject a pending
    # approval get stuffed into the `callback_id` as serialized JSON.
    # pipelineName
    # stageName
    # actionName
    # token
    approved_params = {
        'pipelineName': notification['approval']['pipelineName'],
        'stageName': notification['approval']['stageName'],
        'actionName': notification['approval']['actionName'],
        'token': notification['approval']['token'],
        'value': APPROVED
    }

    rejected_params = {
        'pipelineName': notification['approval']['pipelineName'],
        'stageName': notification['approval']['stageName'],
        'actionName': notification['approval']['actionName'],
        'token': notification['approval']['token'],
        'value': REJECTED
    }

    return {
        'fallback': f"{notification['approval']['pipelineName']} {notification['approval']['stageName']}: {notification['approval']['actionName']}",
        'color': '#FF8400',
        'author_name': notification['approval']['pipelineName'],
        'author_link': notification['consoleLink'],
        'title': f"{notification['approval']['stageName']}: {notification['approval']['actionName']}",
        'title_link': notification['approval']['approvalReviewLink'],
        'text': 'Manual approval required to trigger *ExecuteChangeSet*',
        'footer': notification['region'],
        # ts: (Date.now() / 1000 | 0),
        'mrkdwn_in': ['text'],
        'callback_id': CODEPIPELINE_MANUAL_APPROVAL_CALLBACK,
        'actions': [
            {
                'type': 'button',
                'style': 'primary',
                'name': 'decision',
                'text': 'Approve',
                'value': json.dumps(approved_params),
                'confirm': {
                    'title': 'Production Deploy Approval',
                    'text': 'Are you sure you want to approve this CloudFormation change set for the production stack? Approval will trigger an immediate update to the production stack!',
                    'ok_text': 'Deploy',
                    'dismiss_text': 'Cancel'
                }
            }, {
                'type': 'button',
                'name': 'decision',
                'text': 'Reject',
                'value': json.dumps(rejected_params)
            }
        ]
    }


def slack_message(notification):
    return {
        'attachments': [
            parameters_delta_attachment(notification),
            approval_action_attachment(notification)
        ]
    }


def sns_message(notification):
    return json.dumps(slack_message(notification))


def sns_message_attributes():
    return {
        'WebhookURL': {
            'DataType': 'String',
            'StringValue': os.environ['PIPELINE_SLACK_WEBHOOK_URL']
        }
    }


def lambda_handler(event, context):
    notification = json.loads(event['Records'][0]['Sns']['Message'])

    topic_arn = os.environ['SLACK_MESSAGE_RELAY_TOPIC_ARN']
    message = sns_message(notification)
    message_attributes = sns_message_attributes()

    sns.publish(
        TopicArn=topic_arn,
        Message=message,
        MessageAttributes=message_attributes
    )
