"""
Module implementing an interface with Sentinel Hub Batch service
"""
import logging
import time
import datetime as dt
from dataclasses import field, dataclass
from typing import Optional, Union

from dataclasses_json import config as dataclass_config
from dataclasses_json import dataclass_json, LetterCase, Undefined, CatchAll
from tqdm.auto import tqdm

from .config import SHConfig
from .constants import RequestType
from .data_collections import DataCollection
from .download.sentinelhub_client import SentinelHubDownloadClient
from .geometry import Geometry, BBox, CRS
from .sentinelhub_request import SentinelHubRequest
from .sh_utils import SentinelHubFeatureIterator, remove_undefined
from .sentinelhub_byoc import datetime_config  # TODO

LOGGER = logging.getLogger(__name__)


class SentinelHubBatch:
    """ An interface class for Sentinel Hub Batch API

    For more info check `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#tag/batch_process>`__.
    """
    # pylint: disable=too-many-public-methods

    _REPR_PARAM_NAMES = ['id', 'description', 'bucketName', 'created', 'status', 'userAction', 'valueEstimate',
                         'tileCount']

    def __init__(self, request_id=None, *, request_info=None, config=None):
        """
        :param request_id: A batch request ID
        :type request_id: str or None
        :param request_info: Information about batch request parameters obtained from the service. This parameter can
            be given instead of `request_id`
        :type request_info: dict or None
        :param config: A configuration object
        :type config: SHConfig or None
        """
        if not (request_id or request_info):
            raise ValueError('One of the parameters request_id and request_info has to be given')

        self.request_id = request_id if request_id else request_info['id']
        self.config = config or SHConfig()
        self._request_info = request_info

        self.client = SentinelHubDownloadClient(config=self.config)

    def __repr__(self):
        """ A representation that shows the basic parameters of a batch job
        """
        repr_params = {name: self.info[name] for name in self._REPR_PARAM_NAMES if name in self.info}
        repr_params_str = '\n  '.join(f'{name}: {value}' for name, value in repr_params.items())
        return f'{self.__class__.__name__}({{\n  {repr_params_str}\n  ...\n}})'

    @classmethod
    def create(cls, sentinelhub_request, tiling_grid, output=None, bucket_name=None, description=None, config=None,
               **kwargs):
        """ Create a new batch request

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/createNewBatchProcessingRequest>`__

        :param sentinelhub_request: An instance of SentinelHubRequest class containing all request parameters.
            Alternatively, it can also be just a payload dictionary for Process API request
        :type sentinelhub_request: SentinelHubRequest or dict
        :param tiling_grid: A dictionary with tiling grid parameters. It can be built with `tiling_grid` method
        :type tiling_grid: dict
        :param output: A dictionary with output parameters. It can be built with `output` method. Alternatively, one
            can set `bucket_name` parameter instead.
        :type output: dict or None
        :param bucket_name: A name of an s3 bucket where to save data. Alternatively, one can set `output` parameter
            to specify more output parameters.
        :type bucket_name: str or None
        :param description: A description of a batch request
        :type description: str or None
        :param config: A configuration object
        :type config: SHConfig or None
        :param kwargs: Any other arguments to be added to a dictionary of parameters.
        :return: An instance of `SentinelHubBatch` object that represents a newly created batch request.
        :rtype: SentinelHubBatch
        """
        if isinstance(sentinelhub_request, SentinelHubRequest):
            sentinelhub_request = sentinelhub_request.download_list[0].post_values

        if not isinstance(sentinelhub_request, dict):
            raise ValueError('Parameter sentinelhub_request should be an instance of SentinelHubRequest or a '
                             'dictionary with a request payload')

        payload = {
            'processRequest': sentinelhub_request,
            'tilingGrid': tiling_grid,
            'output': output,
            'bucketName': bucket_name,
            'description': description,
            **kwargs
        }
        payload = remove_undefined(payload)

        url = cls._get_process_url(config)
        client = SentinelHubDownloadClient(config=config)
        request_info = client.get_json(url, post_values=payload, use_session=True)

        return cls(request_info=request_info, config=config)

    @staticmethod
    def tiling_grid(grid_id, resolution, buffer=None, **kwargs):
        """ A helper method to build a dictionary with tiling grid parameters

        :param grid_id: An ID of a tiling grid
        :type grid_id: int
        :param resolution: A grid resolution
        :type resolution: float or int
        :param buffer: Optionally, a buffer around each tile can be defined. It can be defined with a tuple of integers
            `(buffer_x, buffer_y)`, which specifies a number of buffer pixels in horizontal and vertical directions.
        :type buffer: (int, int) or None
        :param kwargs: Any other arguments to be added to a dictionary of parameters
        :return: A dictionary with parameters
        :rtype: dict
        """
        payload = {
            'id': grid_id,
            'resolution': resolution,
            **kwargs
        }
        if buffer:
            payload = {
                **payload,
                'bufferX': buffer[0],
                'bufferY': buffer[1]
            }
        return payload

    @staticmethod
    def output(*, default_tile_path=None, overwrite=None, skip_existing=None, cog_output=None, cog_parameters=None,
               create_collection=None, collection_id=None, responses=None, **kwargs):
        """ A helper method to build a dictionary with tiling grid parameters

        :param default_tile_path: A path or a template on an s3 bucket where to store results. More info at Batch API
            documentation
        :type default_tile_path: str or None
        :param overwrite: A flag specifying if a request should overwrite existing outputs without failing
        :type overwrite: bool or None
        :param skip_existing: A flag specifying if existing outputs should be overwritten
        :type skip_existing: bool or None
        :param cog_output: A flag specifying if outputs should be written in COGs (cloud-optimized GeoTIFFs )or
            normal GeoTIFFs
        :type cog_output: bool or None
        :param cog_parameters: A dictionary specifying COG creation parameters
        :type cog_parameters: dict or None
        :param create_collection: If True the results will be written in COGs and a batch collection will be created
        :type create_collection: bool or None
        :param collection_id: If True results will be added to an existing collection
        :type collection_id: str or None
        :param responses: Specification of path template for individual outputs/responses
        :type responses: list or None
        :param kwargs: Any other arguments to be added to a dictionary of parameters
        :return: A dictionary of output parameters
        :rtype: dict
        """
        return remove_undefined({
            'defaultTilePath': default_tile_path,
            'overwrite': overwrite,
            'skipExisting': skip_existing,
            'cogOutput': cog_output,
            'cogParameters': cog_parameters,
            'createCollection': create_collection,
            'collectionId': collection_id,
            'responses': responses,
            **kwargs
        })

    @staticmethod
    def iter_tiling_grids(config=None, **kwargs):
        """ An iterator over tiling grids

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/getBatchTilingGridsProperties>`__

        :param config: A configuration object
        :type config: SHConfig
        :param kwargs: Any other request query parameters
        :return: An iterator over tiling grid definitions
        :rtype: Iterator[dict]
        """
        url = SentinelHubBatch._get_tiling_grids_url(config)
        return SentinelHubFeatureIterator(
            client=SentinelHubDownloadClient(config=config),
            url=url,
            params=remove_undefined(kwargs),
            exception_message='Failed to obtain information about available tiling grids'
        )

    @staticmethod
    def get_tiling_grid(grid_id, config=None):
        """ Provides a single tiling grid

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/getBatchTilingGridProperties>`__

        :param grid_id: An ID of a requested tiling grid
        :type grid_id: str or int
        :param config: A configuration object
        :type config: SHConfig
        :return: A tiling grid definition
        :rtype: dict
        """
        url = f'{SentinelHubBatch._get_tiling_grids_url(config)}/{grid_id}'
        client = SentinelHubDownloadClient(config=config)
        return client.get_json(url, use_session=True)

    @property
    def info(self):
        """ A dictionary with a Batch request information. It loads a new dictionary only if one doesn't exist yet.

        :return: Batch request info
        :rtype: dict
        """
        if self._request_info is None:
            self.update_info()
        return self._request_info

    def update_info(self):
        """ Updates information about a batch request

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/getSingleBatchProcessRequestById>`__

        :return: Batch request info
        :rtype: dict
        """
        url = self._get_process_url(self.config, request_id=self.request_id)
        self._request_info = self.client.get_json(url, use_session=True)

    @property
    def evalscript(self):
        """ Provides an evalscript used by a batch request

        :return: An evalscript
        :rtype: str
        """
        return self.info['processRequest']['evalscript']

    @property
    def bbox(self):
        """Provides a bounding box used by a batch request

        :return: An area bounding box together with CRS
        :rtype: BBox
        :raises: ValueError
        """
        bbox, _, crs = self._parse_bounds_payload()
        if bbox is None:
            raise ValueError('Bounding box is not defined for this batch request')
        return BBox(bbox, crs)

    @property
    def geometry(self):
        """ Provides a geometry used by a batch request

        :return: An area geometry together with CRS
        :rtype: Geometry
        :raises: ValueError
        """
        _, geometry, crs = self._parse_bounds_payload()
        if geometry is None:
            raise ValueError('Geometry is not defined for this batch request')
        return Geometry(geometry, crs)

    @staticmethod
    def iter_requests(user_id=None, search=None, sort=None, config=None, **kwargs):
        """ Iterate existing batch requests

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/getAllBatchProcessRequests>`__

        :param user_id: Filter requests by a user id who defined a request
        :type user_id: str or None
        :param search: A search query to filter requests
        :type search: str or None
        :param sort: A sort query
        :type sort: str or None
        :param config: A configuration object
        :type config: SHConfig or None
        :param kwargs: Any additional parameters to include in a request query
        :return: An iterator over existing batch requests
        :rtype: Iterator[SentinelHubBatch]
        """
        url = SentinelHubBatch._get_process_url(config)
        params = remove_undefined({
            'userid': user_id,
            'search': search,
            'sort': sort,
            **kwargs
        })
        feature_iterator = SentinelHubFeatureIterator(
            client=SentinelHubDownloadClient(config=config),
            url=url,
            params=params,
            exception_message='No requests found'
        )
        for request_info in feature_iterator:
            yield SentinelHubBatch(request_info=request_info, config=config)

    @staticmethod
    def get_latest_request(config=None):
        """ Provides a batch request that has been created the latest
        """
        latest_request_iter = SentinelHubBatch.iter_requests(
            sort='created:desc',
            count=1,
            config=config
        )
        try:
            return next(latest_request_iter)
        except StopIteration as exception:
            raise ValueError('No batch request is available') from exception

    def update(self, output=None, description=None, **kwargs):
        """ Update batch job request parameters

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/updateBatchProcessRequest>`__

        Similarly to `update_info` method, this method also updates local information in the current instance of
        `SentinelHubBatch`.

        :param output: A dictionary with output parameters to be updated.
        :type output: dict or None
        :param description: A description of a batch request to be updated.
        :type description: str or None
        :param kwargs: Any other arguments to be added to a dictionary of parameters.
        """
        payload = remove_undefined({
            'output': output,
            'description': description,
            **kwargs
        })
        url = self._get_process_url(self.config, request_id=self.request_id)
        self._request_info = self.client.get_json(url, post_values=payload, request_type=RequestType.PUT,
                                                  use_session=True)

    def delete(self):
        """ Delete a batch job request

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/deleteBatchProcessRequest>`__
        """
        url = self._get_process_url(self.config, request_id=self.request_id)
        return self.client.get_json(url, request_type=RequestType.DELETE, use_session=True)

    def start_analysis(self):
        """ Starts analysis of a batch job request

        `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#operation/batchAnalyse>`__
        """
        return self._call_job('analyse')

    def start_job(self):
        """ Starts running a batch job

        `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#operation/batchStartProcessRequest>`__
        """
        return self._call_job('start')

    def cancel_job(self):
        """ Cancels a batch job

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/batchCancelProcessRequest>`__
        """
        return self._call_job('cancel')

    def restart_job(self):
        """ Restarts only those parts of a job that failed

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/batchRestartPartialProcessRequest>`__
        """
        return self._call_job('restartpartial')

    def raise_for_status(self, status='FAILED'):
        """ Raises an error in case batch request has a given status

        :param status: One or more status codes on which to raise an error. The default is `'FAILED'`.
        :type status: str or list(str)
        :raises: RuntimeError
        """
        if isinstance(status, str):
            status = [status]
        batch_status = self.info['status']
        if batch_status in status:
            error_message = self.info.get('error', '')
            formatted_error_message = f' and error message: "{error_message}"' if error_message else ''
            raise RuntimeError(f'Raised for batch request {self.request_id} with status {batch_status}'
                               f'{formatted_error_message}')

    def iter_tiles(self, status=None, **kwargs):
        """ Iterate over info about batch request tiles

        `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#operation/getAllBatchProcessTiles>`__

        :param status: A filter to obtain only tiles with a certain status
        :type status: str or None
        :param kwargs: Any additional parameters to include in a request query
        :return: An iterator over information about each tile
        :rtype: Iterator[dict]
        """
        return SentinelHubFeatureIterator(
            client=self.client,
            url=self._get_tiles_url(),
            params={
                'status': status,
                **kwargs
            },
            exception_message='No tiles found, please run analysis on batch request before calling this method'
        )

    def get_tile(self, tile_id):
        """ Provides information about a single batch request tile

        `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#operation/getBatchTileById>`__

        :param tile_id: An ID of a tile
        :type tile_id: int or None
        :return: Information about a tile
        :rtype: dict
        """
        url = self._get_tiles_url(tile_id=tile_id)
        return self.client.get_json(url, use_session=True)

    def reprocess_tile(self, tile_id):
        """ Reprocess a single failed tile

        `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#operation/restartBatchTileById>`__

        :param tile_id: An ID of a tile
        :type tile_id: int or None
        """
        self._call_job(f'tiles/{tile_id}/restart')

    @staticmethod
    def iter_collections(search=None, config=None, **kwargs):
        """ Iterate over batch collections

        `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#operation/getAllBatchCollections>`__

        :param search: A search query to filter collections
        :type search: str or None
        :param config: A configuration object
        :type config: SHConfig or None
        :param kwargs: Any additional parameters to include in a request query
        :return: An iterator over existing batch collections
        :rtype: Iterator[dict]
        """
        return SentinelHubFeatureIterator(
            client=SentinelHubDownloadClient(config=config),
            url=SentinelHubBatch._get_collections_url(config),
            params={
                'search': search,
                **kwargs
            },
            exception_message='Failed to obtain information about available Batch collections'
        )

    @staticmethod
    def get_collection(collection_id, config=None):
        """ Get batch collection by its id

        `Batch API reference
        <https://docs.sentinel-hub.com/api/latest/reference/#operation/getSingleBatchCollectionById>`__

        :param collection_id: A batch collection id
        :type collection_id: str
        :param config: A configuration object
        :type config: SHConfig or None
        :return: A dictionary of the collection parameters
        :rtype: dict
        """
        url = f'{SentinelHubBatch._get_collections_url(config)}/{collection_id}'
        client = SentinelHubDownloadClient(config=config)
        return client.get_json(url=url, use_session=True)['data']

    @staticmethod
    def create_collection(collection, config=None):
        """ Create a new batch collection

        `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#operation/createNewBatchCollection>`__

        :param collection: Batch collection definition
        :type collection: BatchCollection or dict
        :param config: A configuration object
        :type config: SHConfig or None
        :return: A dictionary of a newly created collection
        :rtype: dict
        """
        url = SentinelHubBatch._get_collections_url(config)
        client = SentinelHubDownloadClient(config=config)
        collection_payload = SentinelHubBatch._to_dict(collection)
        return client.get_json(url=url, post_values=collection_payload, use_session=True)['data']

    @staticmethod
    def update_collection(collection, config=None):
        """ Update an existing batch collection

        `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#operation/updateBatchCollection>`__

        :param collection: Batch collection definition
        :type collection: BatchCollection or dict
        :param config: A configuration object
        :type config: SHConfig or None
        """
        collection_id = SentinelHubBatch._parse_collection_id(collection)
        url = f'{SentinelHubBatch._get_collections_url(config)}/{collection_id}'
        client = SentinelHubDownloadClient(config=config)
        collection_payload = SentinelHubBatch._to_dict(collection)
        return client.get_json(url, post_values=collection_payload, request_type=RequestType.PUT, use_session=True)

    @staticmethod
    def delete_collection(collection, config=None):
        """ Delete an existing batch collection

        `Batch API reference <https://docs.sentinel-hub.com/api/latest/reference/#operation/deleteBatchCollection>`__

        :param collection: Batch collection id or object
        :type collection: str or BatchCollection
        :param config: A configuration object
        :type config: SHConfig or None
        """
        collection_id = SentinelHubBatch._parse_collection_id(collection)
        url = f'{SentinelHubBatch._get_collections_url(config)}/{collection_id}'
        client = SentinelHubDownloadClient(config=config)
        return client.get_json(url=url, request_type=RequestType.DELETE, use_session=True)

    def _parse_bounds_payload(self):
        """ Parses bbox, geometry and crs from batch request payload. If bbox or geometry don't exist it returns None
        instead.
        """
        bounds_definition = self.info['processRequest']['input']['bounds']
        crs = CRS(bounds_definition['properties']['crs'].rsplit('/', 1)[-1])

        return bounds_definition.get('bbox'), bounds_definition.get('geometry'), crs

    def _call_job(self, endpoint_name):
        """ Makes a POST request to the service that triggers a processing job
        """
        process_url = self._get_process_url(request_id=self.request_id, config=self.config)
        url = f'{process_url}/{endpoint_name}'

        return self.client.get_json(url, request_type=RequestType.POST, use_session=True)

    def _get_tiles_url(self, tile_id=None):
        """ Creates an URL for tiles endpoint
        """
        process_url = self._get_process_url(config=self.config, request_id=self.request_id)
        url = f'{process_url}/tiles'
        if tile_id:
            return f'{url}/{tile_id}'
        return url

    @staticmethod
    def _get_process_url(config, request_id=None):
        """ Creates an URL for process endpoint
        """
        url = f'{SentinelHubBatch._get_batch_url(config=config)}/process'
        if request_id:
            return f'{url}/{request_id}'
        return url

    @staticmethod
    def _get_tiling_grids_url(config):
        """ Creates an URL for tiling grids endpoint
        """
        return f'{SentinelHubBatch._get_batch_url(config=config)}/tilinggrids'

    @staticmethod
    def _get_collections_url(config):
        """ Creates an URL for batch collections endpoint
        """
        return f'{SentinelHubBatch._get_batch_url(config=config)}/collections'

    @staticmethod
    def _get_batch_url(config=None):
        """ Creates an URL of the base batch service
        """
        config = config or SHConfig()
        return f'{config.sh_base_url}/api/v1/batch'

    @staticmethod
    def _parse_collection_id(data):
        """ Parses batch collection id from multiple possible inputs
        """
        if isinstance(data, (BatchCollection, DataCollection)):
            return data.collection_id
        if isinstance(data, dict):
            return data['id']
        if isinstance(data, str):
            return data
        raise ValueError(f'Expected a BatchCollection dataclass, dictionary or a string, got {data}.')

    @staticmethod
    def _to_dict(data):
        """ Constructs a dictionary from given object
        """
        if isinstance(data, BatchCollection):
            return data.to_dict()
        if isinstance(data, dict):
            return data
        raise ValueError(f'Expected either a BatchCollection or a dict, got {data}.')


@dataclass_json(letter_case=LetterCase.CAMEL, undefined=Undefined.INCLUDE)
@dataclass
class BatchCollectionAdditionalData:
    """ Dataclass to hold batch collection additionalData part of the payload
    """
    other_data: CatchAll
    bands: Optional[dict] = None


@dataclass_json(letter_case=LetterCase.CAMEL, undefined=Undefined.INCLUDE)
@dataclass
class BatchCollectionBatchData:
    """ Dataclass to hold batch collection batchData part of the payload
    """
    other_data: CatchAll
    tiling_grid_id: Optional[int] = None


@dataclass_json(letter_case=LetterCase.CAMEL, undefined=Undefined.INCLUDE)
@dataclass
class BatchCollection:
    """ Dataclass for batch collection parameters
    """
    name: str
    s3_bucket: str
    other_data: CatchAll
    collection_id: Optional[str] = field(metadata=dataclass_config(field_name='id'), default=None)
    user_id: Optional[str] = None
    created: Optional[dt.datetime] = field(metadata=datetime_config, default=None)
    no_data: Optional[Union[int, float]] = None
    additional_data: Optional[BatchCollectionAdditionalData] = None
    batch_data: Optional[BatchCollectionBatchData] = None

    def to_data_collection(self):
        """ Returns a DataCollection enum for this batch collection
        """
        # TODO: unify and check that collection id exists

        if self.additional_data and self.additional_data.bands:
            band_names = tuple(self.additional_data.bands)
            return DataCollection.define_byoc(collection_id=self.collection_id, bands=band_names)

        return DataCollection.define_byoc(collection_id=self.collection_id)


def get_batch_tiles_per_status(batch_request):
    """ A helper function that queries information about batch tiles and returns information about tiles, grouped by
    tile status.

    :return: A dictionary mapping a tile status to a list of tile payloads.
    :rtype: dict(str, list(dict))
    """
    tiles_per_status = {}

    for tile in batch_request.iter_tiles():
        status = tile['status']
        tiles_per_status[status] = tiles_per_status.get(status, [])
        tiles_per_status[status].append(tile)

    return tiles_per_status


def monitor_batch_job(batch_request, sleep_time=120, analysis_sleep_time=5):
    """ A utility function that keeps checking for number of processed tiles until the given batch request finishes.
    During the process it shows a progress bar and at the end it reports information about finished and failed tiles.

    Notes:

      - Before calling this function make sure to start a batch job by calling `SentinelHubBatch.start_job` method. In
        case a batch job is still being analysed this function will wait until the analysis ends.
      - This function will be continuously collecting tile information from Sentinel Hub service. To avoid making too
        many requests please make sure to adjust `sleep_time` parameter according to the size of your job. Larger jobs
        don't need too frequent tile status updates.
      - Some information about the progress of this function is reported to logging level INFO.

    :param batch_request: A Sentinel Hub batch request object.
    :type batch_request: SentinelHubBatch
    :param sleep_time: Number of seconds to sleep between consecutive progress bar updates.
    :type sleep_time: int
    :param analysis_sleep_time: Number of seconds between consecutive status updates during analysis phase.
    :type analysis_sleep_time: int
    :return: A dictionary mapping a tile status to a list of tile payloads.
    :rtype: dict(str, list(dict))
    """
    batch_request.update_info()
    status = batch_request.info['status']
    while status in ['CREATED', 'ANALYSING', 'ANALYSIS_DONE']:
        LOGGER.info('Batch job has a status %s, sleeping for %d seconds', status, analysis_sleep_time)
        time.sleep(analysis_sleep_time)
        batch_request.update_info()
        status = batch_request.info['status']

    batch_request.raise_for_status(status=['FAILED', 'CANCELED'])

    if status == 'PROCESSING':
        LOGGER.info('Batch job is running')
    finished_count = 0
    success_count = 0
    total_tile_count = batch_request.info['tileCount']
    with tqdm(total=total_tile_count, desc='Progress rate') as progress_bar, \
            tqdm(total=finished_count, desc='Success rate') as success_bar:
        while finished_count < total_tile_count:
            tiles_per_status = get_batch_tiles_per_status(batch_request)
            processed_tiles_num = len(tiles_per_status.get('PROCESSED', []))
            failed_tiles_num = len(tiles_per_status.get('FAILED', []))

            new_success_count = processed_tiles_num
            new_finished_count = processed_tiles_num + failed_tiles_num

            progress_bar.update(new_finished_count - finished_count)
            if new_finished_count != finished_count:
                success_bar.total = new_finished_count
                success_bar.refresh()
            success_bar.update(new_success_count - success_count)

            finished_count = new_finished_count
            success_count = new_success_count

            if finished_count < total_tile_count:
                time.sleep(sleep_time)

    if failed_tiles_num:
        LOGGER.info('Batch job failed for %d tiles', processed_tiles_num)
    return tiles_per_status
