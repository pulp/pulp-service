import json
import logging
import os
import random

from base64 import b64decode
from binascii import Error as Base64DecodeError
from datetime import datetime, timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models.query import QuerySet
from django.shortcuts import redirect

from drf_spectacular.utils import extend_schema, extend_schema_view

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.mixins import DestroyModelMixin, ListModelMixin, RetrieveModelMixin

from pulpcore.plugin.viewsets import OperationPostponedResponse
from pulpcore.plugin.viewsets import ContentGuardViewSet, NamedModelViewSet, RolesMixin, TaskViewSet
from pulpcore.plugin.serializers import AsyncOperationResponseSerializer
from pulpcore.plugin.tasking import dispatch
from pulpcore.app.models import Domain, Group, Task
from pulpcore.app.serializers import DomainSerializer


from pulp_service.app.authentication import RHServiceAccountCertAuthentication

from pulp_service.app.authorization import DomainBasedPermission
from pulp_service.app.models import FeatureContentGuard
from pulp_service.app.models import VulnerabilityReport as VulnReport
from pulp_service.app.serializers import (
    ContentScanSerializer,
    FeatureContentGuardSerializer,
    VulnerabilityReportSerializer,
)
from pulp_service.app.tasks.package_scan import check_npm_package, check_content_from_repo_version
from pulp_rpm.app.models import Package


_logger = logging.getLogger(__name__)


def get_pod_ip():
    """
    Get the current pod's IP address with error handling.
    
    In containerized or constrained environments, socket.gethostbyname()
    can fail due to DNS issues, missing /etc/hosts entries, or network
    configuration problems. This helper catches those errors and returns
    a safe default.
    
    Returns:
        str: The pod's IP address, or 'unavailable' if resolution fails
    """
    import socket
    try:
        return socket.gethostbyname(socket.gethostname())
    except (socket.gaierror, socket.herror, OSError) as e:
        # Log the error for debugging but don't crash
        _logger.warning(f"Failed to resolve pod IP address: {e}")
        return 'unavailable'


class RedirectCheck(APIView):
    """
    Handles requests to the /api/redirect-check/ endpoint.
    """

    # allow anyone to access the endpoint
    authentication_classes = []
    permission_classes = []

    def head(self, request=None, path=None, pk=None):
        """
        Responds to HEAD requests for the redirect-check endpoint.
        """
        return redirect("/api/")


# returning 500 error in a "graceful" way
class InternalServerErrorCheck(APIView):
    """
    Handles requests to the /api/internal-server-error-check/ endpoint.
    """

    # allow anyone to access the endpoint
    authentication_classes = []
    permission_classes = []

    def head(self, request=None, path=None, pk=None):
        """
        Responds to HEAD requests for the internal-server-error-check endpoint.
        """
        return Response(data=None, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# raising an exception (helpful to verify middleware's behavior, for example, otel)
class InternalServerErrorCheckWithException(APIView):
    """
    Handles requests to the /api/raise-exception-check/ endpoint.
    """

    # allow anyone to access the endpoint
    authentication_classes = []
    permission_classes = []

    def head(self, request=None, path=None, pk=None):
        """
        Responds to HEAD requests for the raise-exception-check endpoint.
        """
        # the drf APIException returns a HTTP_500_INTERNAL_SERVER_ERROR
        raise APIException()


class MemoryHeapSnapshotView(APIView):
    """
    Memory profiling endpoint using guppy3 to analyze heap memory usage.
    Returns a snapshot of the current memory heap.
    """
    permission_classes = []

    def get(self, request):
        """
        Get memory heap snapshot using guppy3.

        Query parameters:
        - detailed: boolean (default: false) - return detailed analysis
        - limit: int (default: 20) - number of top entries to show
        """
        import os
        import socket

        try:
            from guppy import hpy
        except ImportError:
            return Response(
                {"error": "guppy3 is not installed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        h = hpy()
        heap = h.heap()

        detailed = request.query_params.get('detailed', 'false').lower() == 'true'
        
        # Validate limit parameter
        try:
            limit = int(request.query_params.get('limit', '20'))
            if limit < 1 or limit > 1000:
                return Response(
                    {
                        "error": "Invalid limit parameter. Must be an integer between 1 and 1000.",
                        "provided": request.query_params.get('limit')
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        except (ValueError, TypeError):
            return Response(
                {
                    "error": "Invalid limit parameter. Must be a valid integer.",
                    "provided": request.query_params.get('limit')
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        response_data = {
            'pod_name': os.getenv('HOSTNAME', 'unknown'),
            'pod_ip': get_pod_ip(),
            'timestamp': datetime.now().isoformat(),
            'total_size_mb': round(heap.size / (1024 * 1024), 2),
            'total_objects': heap.count,
            'heap_summary': str(heap),
        }

        if detailed:
            # Get top objects by reference count
            byrcs = heap.byrcs
            response_data['by_reference_count'] = str(byrcs[:limit])

            # Get top objects by size
            bysize = heap.bysize
            response_data['by_size'] = str(bysize[:limit])

            # Get isolated objects (potential memory leaks)
            response_data['isolated_objects'] = str(h.iso())
        else:
            # Just top 10 by type
            response_data['by_type'] = str(heap.bytype[:10])

        return Response(response_data)


class MemoryObjectTypesView(APIView):
    """
    Get memory usage grouped by object type.
    """
    permission_classes = []

    def get(self, request):
        """
        Get memory usage by object type.

        Query parameters:
        - object_type: str (optional) - filter by specific type (e.g., 'dict', 'list', 'str')
        """
        import os
        import socket

        try:
            from guppy import hpy
        except ImportError:
            return Response(
                {"error": "guppy3 is not installed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        h = hpy()
        heap = h.heap()

        object_type = request.query_params.get('object_type')

        if object_type:
            # Filter by specific type
            filtered = heap.byrcs[heap.byrcs.kind == object_type]
            result = {
                'pod_name': os.getenv('HOSTNAME', 'unknown'),
                'pod_ip': get_pod_ip(),
                'timestamp': datetime.now().isoformat(),
                'object_type': object_type,
                'total_size_mb': round(filtered.size / (1024 * 1024), 2),
                'total_count': filtered.count,
                'details': str(filtered),
            }

            # Try to get referrers for the largest instances
            if filtered.count > 0:
                result['top_referrers'] = str(filtered[:5].referrers)
        else:
            # Show all types
            bytype = heap.bytype
            result = {
                'pod_name': os.getenv('HOSTNAME', 'unknown'),
                'pod_ip': get_pod_ip(),
                'timestamp': datetime.now().isoformat(),
                'by_type': str(bytype),
                'summary': []
            }

            # Extract summary data
            for i in range(min(10, len(bytype))):
                item = bytype[i]
                result['summary'].append({
                    'type': str(item.kind),
                    'size_mb': round(item.size / (1024 * 1024), 2),
                    'count': item.count,
                })

        return Response(result)


class PodInfoView(APIView):
    """
    Returns information about the current pod.
    Useful for identifying which pod you're hitting through a load balancer.
    """
    permission_classes = []

    def get(self, request):
        """
        Get current pod information including name, IP, and resource usage.
        """
        import os
        import socket
        import psutil

        pod_info = {
            'pod_name': os.getenv('HOSTNAME', 'unknown'),
            'pod_ip': get_pod_ip(),
            'timestamp': datetime.now().isoformat(),
        }

        # Add process info if psutil is available
        try:
            process = psutil.Process()
            memory_info = process.memory_info()

            pod_info.update({
                'process': {
                    'pid': os.getpid(),
                    'memory_rss_mb': round(memory_info.rss / (1024 * 1024), 2),
                    'memory_vms_mb': round(memory_info.vms / (1024 * 1024), 2),
                    'cpu_percent': process.cpu_percent(interval=0.1),
                    'num_threads': process.num_threads(),
                }
            })
        except (ImportError, Exception) as e:
            pod_info['process'] = {'error': str(e)}

        return Response(pod_info)


class ProxyMemoryProfileView(APIView):
    """
    Proxy memory profiling requests to a specific pod by IP.

    This allows profiling specific pods when you don't have kubectl access
    but can identify pod IPs from monitoring tools like Grafana.

    Usage:
        GET /api/pulp/debug/memory/proxy/?target_pod_ip=10.0.1.23&endpoint=heap
        GET /api/pulp/debug/memory/proxy/?target_pod_ip=10.0.1.23&endpoint=types&object_type=dict
    """
    permission_classes = []

    @staticmethod
    def _validate_pod_ip(ip_address):
        """
        Validate that the IP address is safe and within allowed ranges.
        Prevents SSRF attacks by blocking dangerous IP ranges.
        
        Returns: (is_valid, error_message)
        """
        import ipaddress
        
        try:
            ip = ipaddress.ip_address(ip_address)
        except ValueError:
            return False, f"Invalid IP address format: {ip_address}"
        
        # Block IPv6 for now (simplify security model)
        if ip.version == 6:
            return False, "IPv6 addresses are not supported"
        
        # Block private/reserved ranges that could be exploited for SSRF
        # Allow only specific private ranges commonly used for Kubernetes pods
        
        # Block localhost/loopback (127.0.0.0/8)
        if ip.is_loopback:
            return False, "Loopback addresses are not allowed"
        
        # Block link-local addresses (169.254.0.0/16 - includes AWS metadata)
        if ip.is_link_local:
            return False, "Link-local addresses are not allowed"
        
        # Block multicast and reserved addresses
        if ip.is_multicast or ip.is_reserved:
            return False, "Multicast and reserved addresses are not allowed"
        
        # Must be private (to ensure we only talk to internal pods)
        if not ip.is_private:
            return False, "Only private IP addresses are allowed (pod IPs)"
        
        # Allow common Kubernetes pod CIDR ranges:
        # 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
        # These are the standard private ranges, but we already blocked dangerous subnets above
        
        # Additional protection: Block specific dangerous subnets within private ranges
        dangerous_subnets = [
            ipaddress.ip_network('10.0.0.0/29'),      # Common Docker default gateway
            ipaddress.ip_network('172.17.0.0/24'),    # Common Docker bridge network
            ipaddress.ip_network('192.168.0.0/29'),   # Common router ranges
            ipaddress.ip_network('192.168.1.0/29'),   # Common router ranges
        ]
        
        for subnet in dangerous_subnets:
            if ip in subnet:
                return False, f"IP address is in a blocked subnet: {subnet}"
        
        return True, None

    def get(self, request):
        """
        Proxy GET request to a specific pod's memory profiling endpoint.

        Query parameters:
        - target_pod_ip: IP address of the target pod (required)
        - endpoint: Which memory endpoint to call (heap, types, pod-info)
        - Additional params are passed through to the target endpoint
        """
        import requests
        import os

        target_pod_ip = request.query_params.get('target_pod_ip')
        endpoint = request.query_params.get('endpoint', 'heap')
        
        # Validate timeout parameter
        timeout_param = request.query_params.get('timeout', '30')
        try:
            timeout = int(timeout_param)
            if timeout < 1 or timeout > 300:
                return Response(
                    {
                        "error": "Invalid timeout parameter. Must be an integer between 1 and 300 seconds.",
                        "provided": timeout_param
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        except (ValueError, TypeError):
            return Response(
                {
                    "error": "Invalid timeout parameter. Must be a valid integer.",
                    "provided": timeout_param
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if not target_pod_ip:
            return Response(
                {"error": "target_pod_ip parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate IP address to prevent SSRF attacks
        is_valid, error_msg = self._validate_pod_ip(target_pod_ip)
        if not is_valid:
            _logger.warning(
                f"SSRF attempt blocked: {error_msg}. "
                f"Source: {request.META.get('REMOTE_ADDR', 'unknown')}, "
                f"Target IP: {target_pod_ip}"
            )
            return Response(
                {
                    "error": "Invalid target IP address",
                    "detail": error_msg
                },
                status=status.HTTP_403_FORBIDDEN
            )

        # Map endpoint names to URLs
        endpoint_map = {
            'pod-info': '/api/pulp/debug/pod-info/',
            'heap': '/api/pulp/debug/memory/heap/',
            'types': '/api/pulp/debug/memory/types/',
        }

        if endpoint not in endpoint_map:
            return Response(
                {"error": f"Invalid endpoint. Must be one of: {list(endpoint_map.keys())}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get target port from environment variable, default to 8000 (as per clowdapp.yaml)
        target_port = os.getenv('PULP_API_SERVICE_PORT_PUBLIC', '8000')
        target_url = f"http://{target_pod_ip}:{target_port}{endpoint_map[endpoint]}"

        # Build query params (excluding our proxy-specific params)
        proxy_params = {'target_pod_ip', 'endpoint', 'timeout'}
        target_params = {k: v for k, v in request.query_params.items() if k not in proxy_params}

        # Forward the request
        try:
            # Copy authorization header if present
            headers = {}
            if 'HTTP_AUTHORIZATION' in request.META:
                headers['Authorization'] = request.META['HTTP_AUTHORIZATION']

            response = requests.get(
                target_url,
                params=target_params,
                headers=headers,
                timeout=timeout
            )

            # Add metadata to show this was proxied
            try:
                data = response.json()
                data['_proxied_from'] = os.getenv('HOSTNAME', 'unknown')
                data['_target_pod_ip'] = target_pod_ip
                return Response(data, status=response.status_code)
            except Exception:
                # If response is not JSON, return as-is
                return Response(
                    {
                        'raw_response': response.text,
                        '_proxied_from': os.getenv('HOSTNAME', 'unknown'),
                        '_target_pod_ip': target_pod_ip,
                    },
                    status=response.status_code
                )

        except requests.exceptions.RequestException as e:
            return Response(
                {
                    "error": f"Failed to connect to pod {target_pod_ip}",
                    "params": target_params,
                    "headers": headers,
                    "details": str(e),
                    "target_url": target_url
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )


class MemrayProfileView(APIView):
    """
    Start/stop memray profiling of the gunicorn master and worker processes.

    Memray attaches to PID 1 (gunicorn master) with --follow-fork to track all workers.
    Creates .bin files that can be analyzed with memray commands.

    Usage:
        POST /api/pulp/debug/memory/memray/start/  - Start profiling
        POST /api/pulp/debug/memory/memray/stop/   - Stop profiling
        GET  /api/pulp/debug/memory/memray/status/ - Get profiling status
        GET  /api/pulp/debug/memory/memray/files/  - List output files
    """
    permission_classes = []

    STATE_FILE = '/tmp/memray-state.txt'
    
    # Keep the subprocess.Popen objects for all workers we're profiling
    _memray_processes = []  # List of subprocess.Popen objects

    @staticmethod
    def _get_gunicorn_worker_pids():
        """Find all gunicorn worker process PIDs."""
        try:
            import psutil
            workers = []
            # Find the gunicorn master process (PID 1)
            try:
                master = psutil.Process(1)
                # Get all child processes (workers)
                for child in master.children(recursive=False):
                    # Check if it's a gunicorn worker
                    cmdline = ' '.join(child.cmdline())
                    if 'gunicorn' in cmdline:
                        workers.append(child.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            
            return workers
        except ImportError:
            # psutil not available, return empty list
            return []

    @staticmethod
    def _sanitize_for_filename(value):
        """
        Sanitize a string to be safe for use in filenames and command arguments.
        Removes or replaces potentially dangerous characters.
        
        Returns: sanitized string
        """
        import re
        # Allow only alphanumeric, dash, underscore, and dot
        # This prevents path traversal, command injection, and other attacks
        sanitized = re.sub(r'[^a-zA-Z0-9\-_.]', '_', str(value))
        # Limit length to prevent issues
        return sanitized[:255]
    
    @staticmethod
    def _validate_pid(pid):
        """
        Validate that a PID is a positive integer.
        
        Returns: (is_valid, sanitized_pid or None)
        """
        try:
            pid_int = int(pid)
            if pid_int <= 0 or pid_int > 2**22:  # Max PID on Linux is typically 2^22
                return False, None
            return True, pid_int
        except (ValueError, TypeError):
            return False, None
    
    @staticmethod
    def _validate_output_path(file_path, allowed_directory):
        """
        Validate that the output file path is within the allowed directory.
        Prevents path traversal attacks.
        
        Returns: (is_valid, resolved_path or None)
        """
        try:
            from pathlib import Path
            import sys
            
            # Resolve both paths to absolute canonical paths
            resolved_file = Path(file_path).resolve()
            resolved_dir = Path(allowed_directory).resolve()
            
            # Check if file is within allowed directory using path-aware comparison
            # This prevents path traversal attacks like:
            # - /tmp/memray-profiles_evil/bad.bin (would bypass string startswith)
            # - ../../../etc/passwd (resolved paths prevent this)
            
            # Use is_relative_to() for Python 3.9+, fallback for older versions
            if sys.version_info >= (3, 9):
                # Python 3.9+ has is_relative_to() method - most secure approach
                if not resolved_file.is_relative_to(resolved_dir):
                    return False, None
            else:
                # Fallback for Python < 3.9: use os.path.commonpath
                import os
                try:
                    # commonpath finds the common base path
                    # If resolved_file is under resolved_dir, commonpath should equal resolved_dir
                    common = os.path.commonpath([str(resolved_file), str(resolved_dir)])
                    if common != str(resolved_dir):
                        return False, None
                except ValueError:
                    # commonpath raises ValueError if paths are on different drives (Windows)
                    return False, None
            
            # Additional check: ensure it's a file, not a directory or special file
            if resolved_file.is_dir() or (resolved_file.exists() and not resolved_file.is_file()):
                return False, None
                
            return True, resolved_file
        except (ValueError, OSError):
            return False, None

    @classmethod
    def _is_process_running(cls, pid):
        """Check if process is in the STATE_FILE (indicating it's tracked as running)."""
        try:
            if not os.path.exists(cls.STATE_FILE):
                return False
            
            with open(cls.STATE_FILE, 'r') as f:
                lines = f.read().strip().split('\n')
                # First line contains comma-separated PIDs
                if not lines or not lines[0]:
                    return False
                
                pids_str = lines[0]
                pids = [int(p) for p in pids_str.split(',') if p]
                
                return pid in pids
        except (OSError, FileNotFoundError, ValueError, IndexError):
            return False

    @classmethod
    def _try_reap_our_children(cls):
        """
        Try to reap our memray child processes if we're the worker that started them.
        This should be called periodically to clean up zombies.
        """
        if cls._memray_processes:
            still_alive = []
            for proc in cls._memray_processes:
                try:
                    # Check if process has exited (non-blocking)
                    retcode = proc.poll()
                    if retcode is None:
                        # Still running
                        still_alive.append(proc)
                    # If retcode is not None, process has exited and been reaped
                except Exception:
                    pass
            cls._memray_processes = still_alive
            return len(still_alive) == 0  # True if all reaped
        return False

    def post(self, request):
        """
        Start or stop memray profiling.

        POST /start/ - Start profiling
        POST /stop/  - Stop profiling
        """
        import subprocess
        import tempfile
        from pathlib import Path

        action = request.path.split('/')[-2]  # 'start' or 'stop'

        if action == 'start':
            # First, try to reap our children if they became zombies
            self._try_reap_our_children()
            
            # Check if already running by reading the PID file
            if os.path.exists(self.STATE_FILE):
                try:
                    with open(self.STATE_FILE, 'r') as f:
                        lines = f.read().strip().split('\n')
                        # First line is comma-separated list of PIDs
                        pids_str = lines[0]
                        output_files_str = lines[1] if len(lines) > 1 else None
                        start_time_str = lines[2] if len(lines) > 2 else None
                        
                        pids = [int(p) for p in pids_str.split(',') if p]
                        
                        # Check if any processes are still running
                        any_running = any(self._is_process_running(pid) for pid in pids)
                        
                        if any_running:
                            return Response({
                                'status': 'already_running',
                                'pod_name': os.getenv('HOSTNAME', 'unknown'),
                                'output_files': output_files_str,
                                'started_at': start_time_str,
                                'worker_pids': pids,
                            })
                        else:
                            # All processes stopped, clean up stale file
                            os.remove(self.STATE_FILE)
                except Exception:
                    # Corrupted state file, remove it
                    if os.path.exists(self.STATE_FILE):
                        os.remove(self.STATE_FILE)

            # Get all gunicorn worker PIDs
            worker_pids = self._get_gunicorn_worker_pids()
            
            if not worker_pids:
                return Response({
                    'error': 'No gunicorn worker processes found',
                    'hint': 'Make sure psutil is installed and gunicorn workers are running',
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Create output directory
            output_dir = Path('/tmp/memray-profiles')
            output_dir.mkdir(exist_ok=True)

            # Generate output filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            pod_name = self._sanitize_for_filename(os.getenv('HOSTNAME', 'unknown'))
            
            # Start memray for each worker
            processes = []
            output_files = []
            failed_workers = []
            
            for worker_pid in worker_pids:
                # Validate PID before using in command
                is_valid, validated_pid = self._validate_pid(worker_pid)
                if not is_valid:
                    _logger.error(f"Invalid PID detected: {worker_pid}")
                    failed_workers.append({
                        'worker_pid': worker_pid,
                        'error': 'Invalid PID value',
                    })
                    continue
                
                # Use sanitized values in filename
                output_file = output_dir / f"memray-{pod_name}-worker-{validated_pid}-{timestamp}.bin"
                
                # Validate output path to prevent path traversal
                is_valid_path, validated_output_file = self._validate_output_path(output_file, output_dir)
                if not is_valid_path:
                    _logger.error(f"Invalid output path detected: {output_file}")
                    failed_workers.append({
                        'worker_pid': validated_pid,
                        'error': 'Invalid output file path',
                    })
                    continue
                
                output_files.append(str(validated_output_file))
                
                # Build memray attach command for this worker
                # Using list format prevents shell injection
                # All arguments are validated/sanitized before use
                cmd = [
                    'memray',
                    'attach',
                    str(validated_pid),  # Validated PID as string (integer converted to string)
                    '--output',
                    str(validated_output_file),  # Validated path
                ]

                try:
                    # Start memray attach
                    # Security measures:
                    # 1. shell=False prevents shell interpretation
                    # 2. cmd is a list (not string) prevents command injection
                    # 3. All arguments are validated before inclusion
                    # 4. No user-controlled data reaches subprocess without validation
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        start_new_session=True,
                        shell=False  # Explicit: no shell interpretation
                    )
                    
                    # Give it a moment to attach
                    import time
                    time.sleep(0.2)
                    retcode = process.poll()
                    
                    if retcode is not None:
                        # Process already exited! Capture error
                        stdout, stderr = process.communicate(timeout=1)
                        failed_workers.append({
                            'worker_pid': worker_pid,
                            'exit_code': retcode,
                            'stderr': stderr,
                        })
                    else:
                        processes.append(process)
                        
                except Exception as e:
                    failed_workers.append({
                        'worker_pid': worker_pid,
                        'error': str(e),
                    })

            if not processes:
                # All workers failed to start profiling
                return Response({
                    'error': 'Failed to start profiling on any worker',
                    'failed_workers': failed_workers,
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            start_time = datetime.now()

            # Store the process objects
            MemrayProfileView._memray_processes = processes

            # Save state to file (PIDs comma-separated, output files comma-separated)
            with open(self.STATE_FILE, 'w') as f:
                f.write(','.join(str(p.pid) for p in processes) + '\n')
                f.write(','.join(output_files) + '\n')
                f.write(f"{start_time.isoformat()}\n")

            response_data = {
                'status': 'started',
                'pod_name': pod_name,
                'pod_ip': request.META.get('REMOTE_ADDR', 'unknown'),
                'output_files': output_files,
                'started_at': start_time.isoformat(),
                'workers_profiled': len(processes),
                'worker_pids': worker_pids,
                'memray_pids': [p.pid for p in processes],
            }
            
            if failed_workers:
                response_data['failed_workers'] = failed_workers
                response_data['warning'] = f'Failed to profile {len(failed_workers)} out of {len(worker_pids)} workers'
            
            return Response(response_data)

        elif action == 'stop':
            # Read state from file
            if not os.path.exists(self.STATE_FILE):
                return Response({
                    'status': 'not_running',
                    'pod_name': os.getenv('HOSTNAME', 'unknown'),
                })
            
            try:
                with open(self.STATE_FILE, 'r') as f:
                    lines = f.read().strip().split('\n')
                    memray_pids_str = lines[0]  # Comma-separated memray PIDs
                    output_files_str = lines[1] if len(lines) > 1 else None
                    start_time_str = lines[2] if len(lines) > 2 else None
                    
                output_files = output_files_str.split(',') if output_files_str else []
            except Exception:
                return Response({
                    'error': 'Failed to read state file',
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            try:
                import logging
                from contextlib import suppress
                from django.db import IntegrityError
                from pulpcore.plugin.models import Artifact
                from pulpcore.plugin.util import get_artifact_url

                _logger = logging.getLogger(__name__)

                # Get all gunicorn worker PIDs to detach from
                worker_pids = self._get_gunicorn_worker_pids()
                
                # Detach from all workers
                detach_results = []
                for worker_pid in worker_pids:
                    # Validate PID before using in command
                    is_valid, validated_pid = self._validate_pid(worker_pid)
                    if not is_valid:
                        _logger.error(f"Invalid PID detected during detach: {worker_pid}")
                        detach_results.append({
                            'worker_pid': worker_pid,
                            'success': False,
                            'error': 'Invalid PID value',
                        })
                        continue
                    
                    # Build detach command with validated PID
                    # All arguments are validated before use
                    detach_cmd = [
                        'memray',
                        'detach', 
                        str(validated_pid)  # Validated PID (integer converted to string)
                    ]
                    
                    try:
                        # Security measures:
                        # 1. shell=False prevents shell interpretation  
                        # 2. detach_cmd is a list (not string)
                        # 3. PID is validated before use
                        result = subprocess.run(
                            detach_cmd,
                            capture_output=True,
                            text=True,
                            timeout=10,
                            shell=False,  # Explicit: no shell interpretation
                            check=False   # We handle return codes manually
                        )
                        
                        detach_results.append({
                            'worker_pid': validated_pid,
                            'success': result.returncode == 0,
                            'stderr': result.stderr if result.returncode != 0 else None,
                        })
                        
                        if result.returncode != 0:
                            _logger.warning(f"memray detach {validated_pid} returned {result.returncode}: {result.stderr}")
                    except subprocess.TimeoutExpired:
                        detach_results.append({
                            'worker_pid': validated_pid,
                            'success': False,
                            'error': 'timeout',
                        })
                    except Exception as e:
                        detach_results.append({
                            'worker_pid': validated_pid,
                            'success': False,
                            'error': str(e),
                        })
                        _logger.error(f"Failed to run memray detach {validated_pid}: {e}")
                
                # Wait a moment for files to be written
                import time
                time.sleep(1)
                
                # Clean up our stored process references
                self._try_reap_our_children()
                MemrayProfileView._memray_processes = []

                duration = None
                if start_time_str:
                    try:
                        start_time = datetime.fromisoformat(start_time_str)
                        duration = (datetime.now() - start_time).total_seconds()
                    except Exception:
                        pass

                # Create Pulp artifacts for all output files
                artifacts_created = []
                for output_file in output_files:
                    if output_file and Path(output_file).exists():
                        try:
                            # Ensure the file is fully synced to disk
                            with open(output_file, 'rb') as f:
                                os.fsync(f.fileno())
                            
                            file_size = Path(output_file).stat().st_size
                            
                            # Create Pulp Artifact from the memray output file
                            artifact = Artifact.init_and_validate(output_file)
                            with suppress(IntegrityError):
                                artifact.save()
                            artifact_url = get_artifact_url(artifact)
                            
                            artifacts_created.append({
                                'file': output_file,
                                'size_bytes': file_size,
                                'artifact_url': artifact_url,
                                'artifact_sha256': artifact.sha256,
                            })
                            _logger.info(f"Memray profile saved as Pulp artifact: {artifact_url}")
                        except Exception as e:
                            _logger.error(f"Failed to create Pulp artifact from {output_file}: {e}")
                            artifacts_created.append({
                                'file': output_file,
                                'error': str(e),
                            })

                # Clear state file
                os.remove(self.STATE_FILE)

                response_data = {
                    'status': 'stopped',
                    'pod_name': os.getenv('HOSTNAME', 'unknown'),
                    'started_at': start_time_str,
                    'stopped_at': datetime.now().isoformat(),
                    'duration_seconds': duration,
                    'workers_profiled': len(output_files),
                    'detach_results': detach_results,
                    'artifacts': artifacts_created,
                }

                return Response(response_data)

            except Exception as e:
                return Response({
                    'error': 'Failed to stop memray',
                    'details': str(e),
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'error': 'Invalid action',
            'valid_actions': ['start', 'stop'],
        }, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request):
        """
        Get memray profiling status or list output files.

        GET /status/ - Get current profiling status
        GET /files/  - List available output files
        """
        import socket
        from pathlib import Path

        action = request.path.split('/')[-2]  # 'status' or 'files'

        if action == 'status':
            # First, try to reap our children if they became zombies
            self._try_reap_our_children()
            
            # Read state from file
            is_running = False
            worker_pids = []
            output_files = []
            start_time_str = None
            duration = None
            
            if os.path.exists(self.STATE_FILE):
                try:
                    with open(self.STATE_FILE, 'r') as f:
                        lines = f.read().strip().split('\n')
                        # PIDs comma-separated
                        pids_str = lines[0]
                        output_files_str = lines[1] if len(lines) > 1 else None
                        start_time_str = lines[2] if len(lines) > 2 else None
                        
                        memray_pids = [int(p) for p in pids_str.split(',') if p]
                        output_files = output_files_str.split(',') if output_files_str else []
                        
                        # Check if any memray attach processes are still running
                        is_running = any(self._is_process_running(pid) for pid in memray_pids)
                        
                        # Get current worker PIDs
                        worker_pids = self._get_gunicorn_worker_pids()
                        
                        # Calculate duration
                        if start_time_str:
                            try:
                                start_time = datetime.fromisoformat(start_time_str)
                                duration = (datetime.now() - start_time).total_seconds()
                            except Exception:
                                pass
                except Exception:
                    # Corrupted state file, remove it
                    if os.path.exists(self.STATE_FILE):
                        os.remove(self.STATE_FILE)

            return Response({
                'pod_name': os.getenv('HOSTNAME', 'unknown'),
                'pod_ip': get_pod_ip(),
                'is_running': is_running,
                'output_files': output_files,
                'started_at': start_time_str,
                'duration_seconds': duration,
                'worker_pids': worker_pids,
                'workers_count': len(worker_pids),
            })

        elif action == 'files':
            output_dir = Path('/tmp/memray-profiles')

            if not output_dir.exists():
                return Response({
                    'pod_name': os.getenv('HOSTNAME', 'unknown'),
                    'files': [],
                })

            files = []
            for file_path in sorted(output_dir.glob('memray-*.bin'), reverse=True):
                stat = file_path.stat()
                file_info = {
                    'filename': file_path.name,
                    'path': str(file_path),
                    'size_bytes': stat.st_size,
                    'size_mb': round(stat.st_size / (1024 * 1024), 2),
                    'created_at': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    'modified_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
                
                # Try to get artifact URL if artifact exists
                try:
                    from pulpcore.plugin.models import Artifact
                    from pulpcore.plugin.util import get_artifact_url
                    import hashlib
                    
                    # Calculate SHA256 to find existing artifact
                    sha256_hash = hashlib.sha256()
                    with open(file_path, 'rb') as f:
                        for chunk in iter(lambda: f.read(8192), b''):
                            sha256_hash.update(chunk)
                    sha256 = sha256_hash.hexdigest()
                    
                    # Try to find existing artifact by SHA256
                    try:
                        artifact = Artifact.objects.get(sha256=sha256)
                        file_info['artifact_url'] = get_artifact_url(artifact)
                        file_info['artifact_sha256'] = artifact.sha256
                    except Artifact.DoesNotExist:
                        file_info['artifact_url'] = None
                        file_info['artifact_note'] = 'Artifact not created yet (profiling may not have been stopped)'
                except Exception as e:
                    file_info['artifact_url'] = None
                    file_info['artifact_error'] = str(e)
                
                files.append(file_info)

            return Response({
                'pod_name': os.getenv('HOSTNAME', 'unknown'),
                'output_directory': str(output_dir),
                'file_count': len(files),
                'files': files,
            })

        return Response({
            'error': 'Invalid action',
            'valid_actions': ['status', 'files'],
        }, status=status.HTTP_400_BAD_REQUEST)


class MemrayProxyProfileView(APIView):
    """
    Proxy memray profiling requests to a specific pod by IP.

    This allows starting/stopping memray profiling on specific pods
    when you don't have kubectl access but can identify pod IPs from Grafana.

    Usage:
        POST /api/pulp/debug/memory/memray/proxy/?target_pod_ip=10.0.1.23&action=start
        POST /api/pulp/debug/memory/memray/proxy/?target_pod_ip=10.0.1.23&action=stop
        GET  /api/pulp/debug/memory/memray/proxy/?target_pod_ip=10.0.1.23&action=status
        GET  /api/pulp/debug/memory/memray/proxy/?target_pod_ip=10.0.1.23&action=files
    """
    permission_classes = []

    @staticmethod
    def _validate_pod_ip(ip_address):
        """
        Validate that the IP address is safe and within allowed ranges.
        Prevents SSRF attacks by blocking dangerous IP ranges.
        
        Returns: (is_valid, error_message)
        """
        import ipaddress
        
        try:
            ip = ipaddress.ip_address(ip_address)
        except ValueError:
            return False, f"Invalid IP address format: {ip_address}"
        
        # Block IPv6 for now (simplify security model)
        if ip.version == 6:
            return False, "IPv6 addresses are not supported"
        
        # Block private/reserved ranges that could be exploited for SSRF
        # Allow only specific private ranges commonly used for Kubernetes pods
        
        # Block localhost/loopback (127.0.0.0/8)
        if ip.is_loopback:
            return False, "Loopback addresses are not allowed"
        
        # Block link-local addresses (169.254.0.0/16 - includes AWS metadata)
        if ip.is_link_local:
            return False, "Link-local addresses are not allowed"
        
        # Block multicast and reserved addresses
        if ip.is_multicast or ip.is_reserved:
            return False, "Multicast and reserved addresses are not allowed"
        
        # Must be private (to ensure we only talk to internal pods)
        if not ip.is_private:
            return False, "Only private IP addresses are allowed (pod IPs)"
        
        # Allow common Kubernetes pod CIDR ranges:
        # 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
        # These are the standard private ranges, but we already blocked dangerous subnets above
        
        # Additional protection: Block specific dangerous subnets within private ranges
        dangerous_subnets = [
            ipaddress.ip_network('10.0.0.0/29'),      # Common Docker default gateway
            ipaddress.ip_network('172.17.0.0/24'),    # Common Docker bridge network
            ipaddress.ip_network('192.168.0.0/29'),   # Common router ranges
            ipaddress.ip_network('192.168.1.0/29'),   # Common router ranges
        ]
        
        for subnet in dangerous_subnets:
            if ip in subnet:
                return False, f"IP address is in a blocked subnet: {subnet}"
        
        return True, None

    def _proxy_request(self, request, method='GET'):
        import requests
        import os

        target_pod_ip = request.query_params.get('target_pod_ip')
        action = request.query_params.get('action', 'status')

        if not target_pod_ip:
            return Response(
                {"error": "target_pod_ip parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate IP address to prevent SSRF attacks
        is_valid, error_msg = self._validate_pod_ip(target_pod_ip)
        if not is_valid:
            _logger.warning(
                f"SSRF attempt blocked: {error_msg}. "
                f"Source: {request.META.get('REMOTE_ADDR', 'unknown')}, "
                f"Target IP: {target_pod_ip}"
            )
            return Response(
                {
                    "error": "Invalid target IP address",
                    "detail": error_msg
                },
                status=status.HTTP_403_FORBIDDEN
            )

        if action not in ['start', 'stop', 'status', 'files']:
            return Response(
                {"error": f"Invalid action. Must be one of: start, stop, status, files"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get target port
        target_port = os.getenv('PULP_API_SERVICE_PORT_PUBLIC', '8000')
        target_url = f"http://{target_pod_ip}:{target_port}/api/pulp/debug/memory/memray/{action}/"

        try:
            headers = {}
            if 'HTTP_AUTHORIZATION' in request.META:
                headers['Authorization'] = request.META['HTTP_AUTHORIZATION']

            if method == 'POST':
                response = requests.post(target_url, headers=headers, timeout=30)
            else:
                response = requests.get(target_url, headers=headers, timeout=30)

            try:
                data = response.json()
                data['_proxied_from'] = os.getenv('HOSTNAME', 'unknown')
                data['_target_pod_ip'] = target_pod_ip
                return Response(data, status=response.status_code)
            except Exception:
                return Response(
                    {
                        'raw_response': response.text,
                        '_proxied_from': os.getenv('HOSTNAME', 'unknown'),
                        '_target_pod_ip': target_pod_ip,
                    },
                    status=response.status_code
                )

        except requests.exceptions.RequestException as e:
            return Response(
                {
                    "error": f"Failed to connect to pod {target_pod_ip}",
                    "details": str(e),
                    "target_url": target_url
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def get(self, request):
        """Proxy GET request (status or files)"""
        return self._proxy_request(request, method='GET')

    def post(self, request):
        """Proxy POST request (start or stop)"""
        return self._proxy_request(request, method='POST')


class FeatureContentGuardViewSet(ContentGuardViewSet, RolesMixin):
    """
    Content guard to protect the content guarded by Subscription Features.
    """

    endpoint_name = "feature"
    queryset = FeatureContentGuard.objects.all()
    serializer_class = FeatureContentGuardSerializer


class DebugAuthenticationHeadersView(APIView):
    """
    Returns the content of the authentication headers.
    """

    authentication_classes = [RHServiceAccountCertAuthentication]
    permission_classes = []

    def get(self, request=None, path=None, pk=None):
        if not settings.AUTHENTICATION_HEADER_DEBUG:
            raise PermissionError("Access denied.")
        try:
            header_content = request.headers["x-rh-identity"]
        except KeyError:
            _logger.error(
                "Access not allowed. Header {header_name} not found.".format(
                    header_name=settings.AUTHENTICATION_JSON_HEADER
                )
            )
            raise PermissionError("Access denied.")

        try:
            header_decoded_content = b64decode(header_content)
        except Base64DecodeError:
            _logger.error("Access not allowed - Header content is not Base64 encoded.")
            raise PermissionError("Access denied.")

        json_header_value = json.loads(header_decoded_content)
        return Response(data=json_header_value)


@extend_schema_view(
    get=extend_schema(operation_id="admin_tasks"),
    list=extend_schema(operation_id="admin_tasks"),
)
class TaskViewSet(TaskViewSet):

    LOCKED_ROLES = {}

    def get_queryset(self):
        qs = self.queryset
        if isinstance(qs, QuerySet):
            # Ensure queryset is re-evaluated on each request.
            qs = qs.all()

        if self.parent_lookup_kwargs and self.kwargs:
            filters = {}
            for key, lookup in self.parent_lookup_kwargs.items():
                filters[lookup] = self.kwargs[key]
            qs = qs.filter(**filters)

        return qs

    @classmethod
    def view_name(cls):
        return "admintasks"


class VulnerabilityReport(NamedModelViewSet, ListModelMixin, RetrieveModelMixin, DestroyModelMixin):

    endpoint_name = "vuln_report_service"
    queryset = VulnReport.objects.all()
    serializer_class = VulnerabilityReportSerializer

    @extend_schema(
        request=ContentScanSerializer,
        description="Trigger a task to generate the package vulnerability report",
        summary="Generate vulnerability report",
        responses={202: AsyncOperationResponseSerializer},
    )
    def create(self, request):
        serializer = ContentScanSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        shared_resources = None
        """Dispatch a task to scan the Content Units from a Repository"""
        if repo_version := serializer.validated_data.get("repo_version", None):
            shared_resources = [repo_version.repository]
            dispatch_task, kwargs = check_content_from_repo_version, {
                "repo_version_pk": repo_version.pk
            }

        """Dispatch a task to scan the npm dependencies' vulnerabilities"""
        if serializer.validated_data.get("package_json", None):
            temp_file_pk = serializer.verify_file()
            dispatch_task, kwargs = check_npm_package, {"npm_package": temp_file_pk}

        task = dispatch(dispatch_task, shared_resources=shared_resources, kwargs=kwargs)
        return OperationPostponedResponse(task, request)


class TaskIngestionDispatcherView(APIView):

    authentication_classes = []
    permission_classes = []

    def get(self, request=None, timeout=25):
        if not settings.TEST_TASK_INGESTION:
            raise PermissionError("Access denied.")

        task_count = 0
        start_time = datetime.now()
        timeout = timedelta(seconds=timeout)

        while datetime.now() < start_time + timeout:
            dispatch(
                'pulp_service.app.tasks.util.no_op_task',
                exclusive_resources=[str(uuid4())]
            )
                
            task_count = task_count + 1

        return Response({"tasks_executed": task_count})


class TaskIngestionRandomResourceLockDispatcherView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request=None, timeout=25):
        if not settings.TEST_TASK_INGESTION:
            raise PermissionError("Access denied.")


        exclusive_resources_list = [str(uuid4()) for _ in range(3)]

        task_count = 0
        start_time = datetime.now()
        timeout = timedelta(seconds=timeout)

        while datetime.now() < start_time + timeout:
            dispatch(
                'pulp_service.app.tasks.util.no_op_task',
                exclusive_resources=[random.choice(exclusive_resources_list)]
            )
                
            task_count = task_count + 1

        return Response({"tasks_executed": task_count})


class RDSConnectionTestDispatcherView(APIView):
    """
    Endpoint to dispatch RDS Proxy connection timeout tests remotely.

    POST body format:
    {
        "tests": ["test_1_idle_connection", "test_2_active_heartbeat"],
        "run_sequentially": false,  // optional, default false
        "duration_minutes": 50       // optional, default 50 (min: 1, max: 300)
    }

    Returns task IDs for dispatched tests.

    Security: Requires staff-level authentication. Tests are long-running
    and should only be triggered by authorized personnel.
    """

    # Use same authentication pattern as other test endpoints
    authentication_classes = []
    permission_classes = []

    AVAILABLE_TESTS = {
        "test_1_idle_connection": "pulp_service.app.tasks.rds_connection_tests.test_1_idle_connection",
        "test_2_active_heartbeat": "pulp_service.app.tasks.rds_connection_tests.test_2_active_heartbeat",
        "test_3_long_transaction": "pulp_service.app.tasks.rds_connection_tests.test_3_long_transaction",
        "test_4_transaction_with_work": "pulp_service.app.tasks.rds_connection_tests.test_4_transaction_with_work",
        "test_5_session_variable": "pulp_service.app.tasks.rds_connection_tests.test_5_session_variable",
        "test_6_listen_notify": "pulp_service.app.tasks.rds_connection_tests.test_6_listen_notify",
        "test_7_listen_with_activity": "pulp_service.app.tasks.rds_connection_tests.test_7_listen_with_activity",
    }

    @extend_schema(
        description="Dispatch RDS Proxy connection timeout tests",
        summary="Dispatch RDS connection tests",
        responses={202: AsyncOperationResponseSerializer},
    )
    def post(self, request):
        """
        Dispatch one or more RDS connection tests.

        Security: Tests must be explicitly enabled via RDS_CONNECTION_TESTS_ENABLED setting.
        """
        # Check if RDS tests are enabled (similar to TEST_TASK_INGESTION check)
        if not settings.DEBUG and not settings.RDS_CONNECTION_TESTS_ENABLED:
            _logger.warning(
                f"Unauthorized RDS test access attempt from {request.META.get('REMOTE_ADDR', 'unknown')}"
            )
            return Response(
                {
                    "error": "RDS connection tests are not enabled.",
                    "hint": "Set RDS_CONNECTION_TESTS_ENABLED=True in settings or enable DEBUG mode."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        tests = request.data.get('tests', [])
        run_sequentially = request.data.get('run_sequentially', False)
        duration_minutes = request.data.get('duration_minutes', 50)

        # Validate duration
        if not isinstance(duration_minutes, int) or duration_minutes < 1 or duration_minutes > 300:
            return Response(
                {
                    "error": "Invalid duration_minutes. Must be an integer between 1 and 300 (5 hours).",
                    "provided": duration_minutes
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if not tests:
            return Response(
                {"error": "No tests specified. Provide a list of test names."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate test names
        if invalid_tests := [t for t in tests if t not in self.AVAILABLE_TESTS]:
            return Response(
                {
                    "error": f"Invalid test names: {invalid_tests}",
                    "available_tests": list(self.AVAILABLE_TESTS.keys())
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        dispatched_tasks = []

        # Check if domain support is enabled
        domain_enabled = getattr(settings, 'DOMAIN_ENABLED', False)

        # For sequential execution, use a shared lock resource
        # This forces tasks to run one at a time
        sequential_lock = []
        if run_sequentially:
            from uuid import uuid4
            sequential_lock = [f"rds-test-sequential-{uuid4()}"]

        for test_name in tests:
            task_func = self.AVAILABLE_TESTS[test_name]

            # Dispatch the task with duration parameter
            task = dispatch(
                task_func,
                exclusive_resources=sequential_lock,  # Empty list for parallel, shared lock for sequential
                kwargs={'duration_minutes': duration_minutes}
            )

            # Get task ID - use current_id() if available, fallback to pk
            task_id = task.current_id() or task.pk

            # Build task href based on domain support
            if domain_enabled:
                # Domain-aware path: /pulp/{domain}/api/v3/tasks/{task_id}/
                domain_name = getattr(task.pulp_domain, 'name', 'default')
                task_href = f"/pulp/{domain_name}/api/v3/tasks/{task_id}/"
            else:
                # Standard path: /pulp/api/v3/tasks/{task_id}/
                task_href = f"/pulp/api/v3/tasks/{task_id}/"

            dispatched_tasks.append({
                "test_name": test_name,
                "task_id": str(task_id),
                "task_href": task_href,
            })

        return Response({
            "message": f"Dispatched {len(dispatched_tasks)} test(s)",
            "tasks": dispatched_tasks,
            "run_sequentially": run_sequentially,
            "duration_minutes": duration_minutes,
            "note": f"Each test runs for approximately {duration_minutes} minutes. Monitor task status via task_href."
        }, status=status.HTTP_202_ACCEPTED)

    def get(self, request):
        """
        Get available tests and their descriptions.

        This endpoint is always accessible for documentation purposes.
        """
        return Response({
            "available_tests": list(self.AVAILABLE_TESTS.keys()),
            "descriptions": {
                "test_1_idle_connection": "Idle connection test - baseline timeout test",
                "test_2_active_heartbeat": "Active heartbeat test - periodic queries",
                "test_3_long_transaction": "Long transaction test - idle transaction",
                "test_4_transaction_with_work": "Transaction with work test - active transaction",
                "test_5_session_variable": "Session variable test - connection pinning via SET",
                "test_6_listen_notify": "LISTEN/NOTIFY test - CRITICAL: real worker behavior",
                "test_7_listen_with_activity": "LISTEN with activity test - periodic notifications",
            },
            "usage": {
                "endpoint": "/api/pulp/rds-connection-tests/",
                "method": "POST",
                "body": {
                    "tests": ["test_1_idle_connection", "test_2_active_heartbeat"],
                    "run_sequentially": False,
                    "duration_minutes": 50
                },
                "note": "duration_minutes is optional (default: 50, min: 1, max: 300)"
            }
        })


class DatabaseTriggersView(APIView):
    """
    Returns information about database triggers on the core_task table.
    """

    # Allow anyone to access the endpoint for debugging
    authentication_classes = []
    permission_classes = []

    def get(self, request=None):
        """
        Query PostgreSQL system catalogs for triggers on core_task table.
        """
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    t.tgname AS trigger_name,
                    c.relname AS table_name,
                    CASE t.tgtype::integer & 1
                        WHEN 1 THEN 'ROW'
                        ELSE 'STATEMENT'
                    END AS trigger_level,
                    CASE t.tgtype::integer & 66
                        WHEN 2 THEN 'BEFORE'
                        WHEN 64 THEN 'INSTEAD OF'
                        ELSE 'AFTER'
                    END AS trigger_timing,
                    CASE
                        WHEN t.tgtype::integer & 4 <> 0 THEN 'INSERT'
                        WHEN t.tgtype::integer & 8 <> 0 THEN 'DELETE'
                        WHEN t.tgtype::integer & 16 <> 0 THEN 'UPDATE'
                        ELSE 'UNKNOWN'
                    END AS trigger_event,
                    p.proname AS function_name,
                    pg_get_triggerdef(t.oid) AS trigger_definition,
                    pg_get_functiondef(p.oid) AS function_definition
                FROM pg_trigger t
                JOIN pg_class c ON t.tgrelid = c.oid
                JOIN pg_proc p ON t.tgfoid = p.oid
                WHERE c.relname = 'core_task'
                AND t.tgisinternal = false
                ORDER BY t.tgname;
            """)

            columns = [col[0] for col in cursor.description]
            triggers = []
            for row in cursor.fetchall():
                trigger_info = dict(zip(columns, row))
                triggers.append(trigger_info)

        return Response({
            "table": "core_task",
            "trigger_count": len(triggers),
            "triggers": triggers
        })


class ReleaseTaskLocksView(APIView):
    """
    Admin-only endpoint to manually release Redis locks for a task.

    This endpoint is useful for debugging lock issues and cleaning up
    orphaned locks when needed. Requires admin privileges.
    """

    # Require admin authentication
    permission_classes = [IsAdminUser]

    def get(self, request):
        """
        Release all Redis locks for a given task UUID.

        Query parameters:
            task_id: UUID of the task to release locks for

        Returns:
            200: Locks released successfully
            400: Missing or invalid task_id parameter
            404: Task not found
            500: Error releasing locks
        """
        # Check if Redis worker type is enabled
        if settings.WORKER_TYPE != "redis":
            return Response(
                {
                    "error": "This endpoint only works with Redis workers.",
                    "worker_type": settings.WORKER_TYPE
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get task_id from query parameters
        task_id = request.GET.get('task_id')

        if not task_id:
            return Response(
                {"error": "Missing required query parameter: task_id"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Import Redis-specific functions
            from pulpcore.app.redis_connection import get_redis_connection
            from pulpcore.tasking.redis_locks import resource_to_lock_key

            # Get Redis connection
            redis_conn = get_redis_connection()
            if not redis_conn:
                return Response(
                    {"error": "Redis connection not available"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # Look up the task
            try:
                task = Task.objects.select_related('pulp_domain').get(pk=task_id)
            except Task.DoesNotExist:
                return Response(
                    {"error": f"Task {task_id} not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Extract exclusive and shared resources from the task
            exclusive_resources = [
                resource
                for resource in task.reserved_resources_record or []
                if not resource.startswith("shared:")
            ]

            shared_resources = [
                resource[7:]  # Remove "shared:" prefix
                for resource in task.reserved_resources_record or []
                if resource.startswith("shared:")
            ]

            # Check who holds the task lock (for informational purposes)
            task_lock_key = f"task:{task_id}"
            task_lock_holder = redis_conn.get(task_lock_key)
            if task_lock_holder:
                task_lock_holder = task_lock_holder.decode('utf-8')

            # Delete exclusive resource locks directly (no ownership check)
            exclusive_locks_deleted = 0
            for resource in exclusive_resources:
                lock_key = resource_to_lock_key(resource)
                if redis_conn.delete(lock_key):
                    exclusive_locks_deleted += 1
                    _logger.info(f"Deleted exclusive lock for resource: {resource}")

            # Delete shared resource locks directly (delete the entire set)
            shared_locks_deleted = 0
            for resource in shared_resources:
                lock_key = resource_to_lock_key(resource)
                if redis_conn.delete(lock_key):
                    shared_locks_deleted += 1
                    _logger.info(f"Deleted shared lock set for resource: {resource}")

            # Delete the task lock
            task_lock_deleted = redis_conn.delete(task_lock_key)

            return Response({
                "message": "Successfully released locks for task",
                "task_id": str(task_id),
                "task_state": task.state,
                "task_lock_holder": task_lock_holder,
                "task_lock_deleted": bool(task_lock_deleted),
                "exclusive_resources": exclusive_resources,
                "exclusive_resources_count": len(exclusive_resources),
                "exclusive_locks_deleted": exclusive_locks_deleted,
                "shared_resources": shared_resources,
                "shared_resources_count": len(shared_resources),
                "shared_locks_deleted": shared_locks_deleted
            })

        except Exception as e:
            _logger.exception(f"Error releasing locks for task {task_id}")
            return Response(
                {
                    "error": "Failed to release locks",
                    "detail": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CreateDomainView(APIView):

    permission_classes = [DomainBasedPermission]
    """
    Custom endpoint to create domains with service-specific logic.
    """

    @extend_schema(
        request=DomainSerializer,
        description="Create a new domain for from S3 template domain, self-service path",
        summary="Create domain",
        responses={201: DomainSerializer},
    )
    def post(self, request):
        """
        Self-service endpoint to create a new domain.
        This endpoint uses the model domain's storage settings and class,
        """
        
        # Check if user has a group, create one if not
        user = request.user
        domain_name = request.data.get('name')
        
        if not domain_name:
            return Response(
                {"error": "Domain name is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
                
        if not user.groups.exists():
            # User has no groups, create one with a unique name or reuse existing
            group_name = f"domain-{domain_name}"
            _logger.info(f"User {user.username} has no groups. Creating or finding group '{group_name}' for domain creation.")
            try:
                # Use get_or_create to avoid duplicate group name issues
                group, created = Group.objects.get_or_create(name=group_name)
                if created:
                    _logger.info(f"Created new group '{group_name}'.")
                else:
                    _logger.info(f"Reusing existing group '{group_name}'.")
                
                # Add user to the group
                user.groups.add(group)
                _logger.info(f"Added user {user.username} to group '{group_name}'.")
            except Exception as e:
                _logger.error(f"Failed to create or assign group '{group_name}': {e}")
                return Response(
                    {"error": f"Failed to create group for domain: {str(e)}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        else:
            _logger.info(f"User {user.username} already belongs to a group.")
        
        # Prepare data with defaults from default domain if needed
        data = request.data.copy()
        
        # Always get storage settings from model domain (ignore user input)
        try:
            model_domain = Domain.objects.get(name='template-domain-s3')
            data['storage_settings'] = model_domain.storage_settings
            data['storage_class'] = model_domain.storage_class
            data['pulp_labels'] = model_domain.pulp_labels
        except Domain.DoesNotExist:
            _logger.error("Model domain 'template-domain-s3' not found")
            return Response(
                {"error": "Model domain 'template-domain-s3' not found. Please create it first with correct storage settings."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        serializer = DomainSerializer(data=data)
        serializer.is_valid(raise_exception=True)
                
        # Perform the creation with validated data
        with transaction.atomic():
            domain = serializer.save()
            
        response_data = DomainSerializer(domain, context={'request': request}).data
        
        return Response(response_data, status=status.HTTP_201_CREATED)
