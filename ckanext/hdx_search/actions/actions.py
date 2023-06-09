import json
import logging
import requests

from six.moves.urllib.parse import urlencode

import ckan.lib.munge as munge
import ckan.model as model
import ckan.plugins.toolkit as tk
import ckanext.hdx_search.helpers.qa_data as qa_data

log = logging.getLogger(__name__)

get_action = tk.get_action
_check_access = tk.check_access
side_effect_free = tk.side_effect_free
config = tk.config
ValidationError = tk.ValidationError


def populate_related_items_count(context, data_dict):
    pkg_dict_list = data_dict.get('pkg_dict_list', {})
    for pkg_dict in pkg_dict_list:
        pkg = model.Package.get(pkg_dict['id'])
        _check_access('package_show', context, pkg_dict)
        # rel_items = get_action('related_list')(context, {'id': pkg_dict['id']})
        pkg_dict['related_count'] = 0
    return pkg_dict_list


def populate_showcase_items_count(context, data_dict):
    pkg_dict_list = data_dict.get('pkg_dict_list', {})
    for pkg_dict in pkg_dict_list:
        pkg = model.Package.get(pkg_dict['id'])
        # _check_access('package_show', context, pkg_dict)
        if pkg:
            try:
                # showcase_items = get_action('ckanext_package_showcase_list')(context, {'package_id': pkg_dict.get('id')})
                _check_access('package_show', context, pkg_dict)
                pkg_dict['showcase_count'] = len(
                    hdx_get_package_showcase_id_list(context, {'package_id': pkg_dict.get('id')}))
            except Exception as e:
                log.info('Package id' + pkg_dict.get('id') + ' not found')
                log.exception(e)
    return pkg_dict_list


# code adapted from ckanext-showcase.../logic/action/get.py:94
def hdx_get_package_showcase_id_list(context, data_dict):
    from ckan.lib.navl.dictization_functions import validate
    from ckanext.showcase.logic.schema import (package_showcase_list_schema)
    from ckanext.showcase.model import ShowcasePackageAssociation

    _check_access('ckanext_package_showcase_list', context, data_dict)
    # validate the incoming data_dict
    validated_data_dict, errors = validate(data_dict, package_showcase_list_schema(), context)

    if errors:
        raise ValidationError(errors)

    # get a list of showcase ids associated with the package id
    showcase_id_list = ShowcasePackageAssociation.get_showcase_ids_for_package(validated_data_dict['package_id'])
    return showcase_id_list


@side_effect_free
def hdx_qa_questions_list(context, data_dict):
    return qa_data.questions_list


@side_effect_free
def hdx_qa_sdcmicro_run(context, data_dict):
    '''
    Add sdc micro flag "running" to resource
    Post to aws endpoint to start the sdc micro check
    parameters for R script:
    -d "idp_settlement|settlement|resp_gender|resp_age|breadwinner|total_hh|person_with_disabilities" -w weights_general -s Feuil1 -f data11.xlsx -t "text|text|text|text|numeric|text|text|text|text|text|numeric|text|numeric"
    :param data_dict: dictionary containg parameters
    :type data_dict: dict
    Parameters from data_dict
    :param dataset_id: the id or name of the dataset
    :type dataset_id: str
    :param resource_id: the id or name of the resource
    :type resource_id: str
    :param data_columns_list: list with data columns
    :param weight_column: the weight column
    :param columns_type_list: list with types for each column - text, double, numeric, date
    :param sheet: in case of excel/xlsx/xls we need the sheet id (0-n)
    :param skip_rows: how many rows to skip until data
    :return: True or False or data_dict
    :rtype: bool
    '''
    # resource_patch to mark sdc micro flag in "running" mode
    # post to aws endpoint to start the sdc micro (sdc micro will have to mark the flag and upload the result)
    _check_access('qa_sdcmicro_run', context, {})
    resource_id = data_dict.get("resource_id")
    if resource_id:
        try:
            # resource_dict = get_action("resource_show")(context, {"id": resource_id})
            resource_dict = get_action("hdx_qa_resource_patch")(context, {"id": resource_id, "sdc_report_flag": "QUEUED"})
            _run_sdcmicro_check(resource_dict, data_dict.get("data_columns_list"), data_dict.get("weight_column"),
                                data_dict.get("columns_type_list"), data_dict.get("sheet", 0), context)
        except Exception as e:
            ex_msg = e.message if hasattr(e, 'message') and e.message else str(e)
            message = e.error_summary if hasattr(e, 'error_summary') and e.error_summary else 'Something went wrong while processing the request: ' + str(ex_msg)
            raise ValidationError({'message': message}, error_summary=message)
    else:
        return {
            'message': "Resource ID not provided or not found"
        }
    return data_dict


@side_effect_free
def hdx_qa_pii_run(context, data_dict):
    '''
    Add sdc micro flag "running" to resource
    Post to aws endpoint to start the sdc micro check
    :param data_dict: dictionary containg parameters
    :type data_dict: dict
    Parameters from data_dict
    :param dataset_id: the id or name of the dataset
    :type dataset_id: str
    :param resource_id: the id or name of the resource
    :type resource_id: str
    :return: True or False or data_dict
    :rtype: bool
    '''
    # resource_patch to mark sdc micro flag in "running" mode
    # post to aws endpoint to start the sdc micro (sdc micro will have to mark the flag and upload the result)
    _check_access('qa_pii_run', context, {})
    resource_id = data_dict.get("resourceId")
    if resource_id:
        try:
            resource_dict = get_action("resource_show")(context, {"id": resource_id})
            old_pii_report_flag = resource_dict.get('pii_report_flag',"")
            _run_pii_check(resource_dict, context)
            get_action("hdx_qa_resource_patch")(context, {"id": resource_id, "pii_report_flag": "QUEUED"})
        except Exception as e:
            get_action("hdx_qa_resource_patch")(context, {"id": resource_id, "pii_report_flag": old_pii_report_flag})
            ex_msg = e.message if hasattr(e, 'message') and e.message else str(e)
            message = e.error_summary if hasattr(e, 'error_summary') and e.error_summary else 'Something went wrong while processing the request:' + str(ex_msg)
            raise ValidationError({'message': message}, error_summary=message)
    else:
        return json.dumps({'success': False, 'error': {'message': 'Resource ID not provided or not found'}})
    return True


PII_RUN_URL = config.get('hdx.echo_url', "https://1oelc8tsn7.execute-api.eu-central-1.amazonaws.com") + "/addpii"
SDCMICRO_RUN_URL = config.get('hdx.echo_url', "https://1oelc8tsn7.execute-api.eu-central-1.amazonaws.com") + "/addsdc"
AWS_RESOURCE_FORMAT = "resources/{resource_id}/{resource_name}"
ECHO_API_KEY = config.get('hdx.echo_api_key', '')
AWS_LOG_UPDATE = config.get('hdx.echo_url', "https://1oelc8tsn7.execute-api.eu-central-1.amazonaws.com") + "/amendjson"

def _run_pii_check(resource_dict, context):
    try:
        munged_resource_name = _get_resource_s3_path(resource_dict)
        data_dict = {
            # 'resourcePath': AWS_RESOURCE_FORMAT.format(resource_id=resource_dict.get("id"),
            #                                            resource_name=munged_resource_name),
            'resourceId': AWS_RESOURCE_FORMAT.format(resource_id=resource_dict.get("id"),
                                                     resource_name=munged_resource_name)
            # 'sheet': sheet,
            # 'riskThreshold': 3
        }
        # data_dict['resourceProxyUrl'] = _set_resource_proxy_url(context, data_dict, resource_dict, None)
        # log.warn('RunPII for resourceId [%s], resourcePath [%s], resourceProxyUrl [%s]' % (data_dict['resourceId'], data_dict['resourcePath'], data_dict['resourceProxyUrl']))
        r = requests.post(
            PII_RUN_URL,
            headers={
                'Content-Type': 'application/json',
                'X-Api-Key': ECHO_API_KEY
            },
            data=json.dumps(data_dict))
        r.raise_for_status()
    except requests.exceptions.ConnectionError as ex:
        log.error(ex)
        raise ex
    except Exception as ex:
        log.error(ex)
        raise ex
    return True


def _run_sdcmicro_check(resource_dict, data_columns_list, weightColumn=None, columns_type_list=None, sheet=0, context=None):
    try:
        munged_resource_name = _get_resource_s3_path(resource_dict)
        data_dict = {
            'resourcePath': AWS_RESOURCE_FORMAT.format(resource_id=resource_dict.get("id"),
                                                       resource_name=munged_resource_name),
            'sheet': sheet,
            'riskThreshold': 3
        }
        if data_columns_list:
            data_dict['columnNames'] = '|'.join(map(str, data_columns_list))
        if weightColumn:
            data_dict['weightColumn'] = str(weightColumn)
        if columns_type_list:
            data_dict['columnTypes'] = '|'.join(map(str, columns_type_list))

        data_dict['resourceProxyUrl'] = _set_resource_proxy_url(context, data_dict, resource_dict, sheet)
        log.warn('RunSDC for resourcePath [%s], resourceProxyUrl [%s], sheet [%s]' % (data_dict['resourcePath'], data_dict['resourceProxyUrl'], data_dict['sheet']))
        r = requests.post(
            SDCMICRO_RUN_URL,
            headers={
                'Content-Type': 'application/json',
                'X-Api-Key': ECHO_API_KEY
            },
            data=json.dumps(data_dict))
        r.raise_for_status()
    except requests.exceptions.ConnectionError as ex:
        log.error(ex)
        raise ex
    except Exception as ex:
        log.error(ex)
        raise ex
    return True


def _set_resource_proxy_url(context, data_dict, resource_dict, sheet=None):
    proxy_data_preview_url = config.get('hdx.hxlproxy.url') + '/api/data-preview.csv'
    url = get_action("hdx_get_s3_link_for_resource")(context, {"id": resource_dict.get("id")})
    params_dict = {'url': url.get('s3_url', resource_dict.get("download_url") or resource_dict.get("hdx_rel_url"))}
    if sheet:
        params_dict['sheet'] = sheet
    params = urlencode(params_dict)
    # {'sheet': sheet, 'url': resource_dict.get("download_url") or resource_dict.get("hdx_rel_url")})
    return proxy_data_preview_url + '?{params}'.format(params=params)


def _get_resource_s3_path(resource_dict):
    download_url = resource_dict.get("download_url") or resource_dict.get("hdx_rel_url")
    if "download/" in download_url:
        url = download_url.split("download/")[1]
    else:
        url = resource_dict.get("name")
    munged_resource_name = munge.munge_filename(url)
    return munged_resource_name


# @side_effect_free
def aws_log_update(context, data_dict):
    '''
    Add key/value to the json log from aws
    :param data_dict: dictionary containg parameters
    :type data_dict: dict
    Parameters from data_dict
    :param resource_id: the id or name of the resource
    :type resource_id: str
    :param filename: name of the log file
    :type filename: str
    :param key: key of the property we need to update
    :type key: str
    :param value: value of the property we need to update
    :type value: str
    :return: True or False or data_dict
    :rtype: bool
    '''

    # post to aws endpoint to update the json log file
    _check_access('qa_pii_run', context, {})
    resource_id = data_dict.get("resourceId")
    if resource_id:
        try:
            # resource_dict = get_action("resource_show")(context, {"id": resource_id})
            # data_dict['resource_dict'] = resource_dict
            dlpRun = data_dict.get("dlpRun", 'False')
            if dlpRun == 'True':
                _run_aws_log_update(context, data_dict)
        except Exception as e:
            ex_msg = e.message if hasattr(e, 'message') and e.message else str(e)
            message = e.error_summary if hasattr(e, 'error_summary') and e.error_summary else 'Something went wrong while processing the request:' + str(
                ex_msg)
            raise ValidationError({'message': message}, error_summary=message)
    else:
        return json.dumps({'success': False, 'error': {'message': 'Resource ID not provided or not found'}})
    return True


def _run_aws_log_update(context, data_dict):
    try:
        data_dict['filename'] = data_dict.get('filename').split('/')[-1:][0]

        log.warn('Run aws log update for resourceId [%s]' % (data_dict['resourceId']))
        r = requests.post(
            AWS_LOG_UPDATE,
            headers={
                'Content-Type': 'application/json',
                'X-Api-Key': ECHO_API_KEY
            },
            data=json.dumps(data_dict))
        r.raise_for_status()
    except requests.exceptions.ConnectionError as ex:
        log.error(ex)
        raise ex
    except Exception as ex:
        log.error(ex)
        raise ex
    return True
