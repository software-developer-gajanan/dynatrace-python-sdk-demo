# dynatrace-python-sdk-demo
Python demo app traced with the Dynatrace OneAgent SDK, with an SOP
Standard Operating Procedure
Tracing a Python application with the Dynatrace OneAgent SDK - setup, verification and failure analysis
Applies to
Python applications on hosts with Dynatrace OneAgent (full-stack). Tested on Linux; production target is Windows Server.
Owner
Dynatrace / Monitoring team
Prepared by
Gajanan Kalkundre
Reviewed by
Done

1. Purpose and scope
Dynatrace OneAgent detects a Python process and shows host and process metrics, but it does not create services or traces for Python code on its own. This SOP shows how to add request-level tracing with the OneAgent SDK for Python, how to confirm it in Dynatrace, and how to test and analyse failures.
A small demo application (sample_app.py) is used throughout, so every step can be checked before the same pattern is applied to a real application.


Without the SDK
With the SDK
What Dynatrace shows
Host, process, CPU, memory, network
Services, endpoints, traces (PurePaths), SQL calls, outgoing calls, failures
Code change needed
None
Yes - tracer calls added to the application

2. Prerequisites
Item
Requirement
OneAgent
Installed and running on the host, full-stack mode, version 1.251 or newer.
Python
CPython 3.8 or newer. x86 / x86-64 only. On Linux the system must be glibc based (Alpine/musl is not supported).
Packages
oneagent-sdk (installed in a virtual environment). The demo application uses only the standard library.
Network
Browser access to the application port (8085 in this SOP). The application's outgoing call needs its target URL to be reachable (optional).
Rights
Install and run as a normal user. Do not use sudo for the virtual environment or pip.

Module 1 - Prepare the environment
Go to the application folder and create a virtual environment as your own user.
Install the SDK inside the virtual environment.
Check that the SDK can find the OneAgent.
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install oneagent-sdk
python -c "import oneagent; print(oneagent.initialize())"

Expected result: the last command prints STATUS_INITIALIZED with error=None (Figure 1). If it prints "Could not load agent", the SDK is installed but the OneAgent is not running on this host.


Figure 1 - SDK check - output from the test server
Windows Server: use the same steps with py -m venv venv, venv\Scripts\activate and py -m pip install oneagent-sdk.
Module 2 - Run the application
Stop any old copy: pkill -f sample_app.py.
Start the application in server mode with the virtual environment's Python.
Read the first log lines. They must show agent_state=0 (ACTIVE) (Figure 2). Any other state means no traces will be sent.
From a second terminal, check the health endpoint (Figure 3).
Open http://<server-ip>:8085/ in a browser (Figure 4).
venv/bin/python sample_app.py --mode server --port 8085



Figure 2 - Application start - expected output (example timestamps)


Figure 3 - Health check - output from the test server


Figure 4 - Application UI: normal endpoints, red failure-scenario buttons, last response and recent orders
Useful settings (environment variables): APP_URL (target of the outgoing call), APP_TIMEOUT, APP_SERVICE_NAME (service name in Dynatrace, default SampleApp), APP_DB and APP_LOG.
Module 3 - Confirm the process is detected
In Dynatrace open Compute > Processes (or open the host and use its Processes section).
Find the process by the application's script name. It has its own process group.
Check CPU, memory and network on the process page. The service is not listed yet unless the SDK is already active.


Figure 5 - Process detected in Dynatrace (process-level monitoring, no traces yet)
Module 4 - SDK integration in the code
Every SDK change in the application is wrapped in comments, so all of them can be found by searching the file for SDK ADDED.
Step
Where in the code
What it adds
Seen in Dynatrace as
1
Top of the file
import oneagent
-
2
init_sdk()
Initializes the SDK, logs the agent state, creates reusable database and web application info
Agent state in the log
3
db() helper
trace_sql_database_request around every SQL statement
SQL spans (INSERT, SELECT, UPDATE, DELETE)
4
http_get() helper
trace_outgoing_web_request, passes the Dynatrace tag in the request header
Outgoing GET span, links to the called service
5
Business steps (run_cycle, checkout_flow, ...)
trace_custom_service for internal steps, custom request attributes, optional trace ID in log lines
Internal steps and filterable attributes
6
Handler.traced()
trace_incoming_web_request for each business endpoint (not for health, UI page or UI data)
Request / endpoint such as /work
7
main()
Clean shutdown on stop (oneagent.shutdown())
-


The pattern is the same everywhere: open a tracer with a with-block, do the work inside it. An exception that leaves the block marks that step as failed.
with sdk.trace_custom_service("run_cycle", "SampleApp"):    # step appears as its own span
    fetch_remote_data()                                      # outgoing call (child span)
    store_records(5)                                         # SQL statements (child spans)

Note: SQL spans are only recorded inside another SDK trace. In this application every SQL call runs inside a traced endpoint or step.
Module 5 - Verify traces in Dynatrace
Generate traffic: click the endpoint buttons in the UI (or use the auto traffic button).
In Dynatrace open Distributed Tracing > Explorer, set the time range to the last 30 minutes and refresh. Allow a few minutes for the first data. If the Requests view says "No matching requests", click Change to spans.
Check that the service name (SampleApp) and the endpoints are listed (Figures 6 and 8).
Open one trace and check the structure: request, internal step, outgoing call and SQL statements (Figure 7).
Use Group by > Endpoint to compare counts and response times per endpoint.


Figure 6 - First traces after the SDK integration: /work and its internal step run_cycle


Figure 7 - Trace detail: request, run_cycle, outgoing call and SQL statements, with the linked log line


Figure 8 - Multi-endpoint version: each endpoint is listed separately with its own duration and status
Module 6 - Failure scenarios
Purpose: confirm that failures are visible in Dynatrace with enough detail to find the cause. The application has five dedicated failure endpoints (red buttons in the UI), each producing a different kind of failure, plus two older ones. Each failure is reported to Dynatrace through the SDK (the failed tracer carries the exception type and message).
6.1 Failure endpoints
Endpoint
What fails
HTTP
What to look for in Dynatrace
/fail/exception
Exception inside a traced step (invalid number). With ?qty=5 it succeeds.
500
Failed request and failed step validate_order with the exception text
/fail/db
SQL error - table does not exist
500
Failed SQL span under the failed request
/fail/downstream
Outgoing call to a service that is down
502
Failed outgoing-call span (connection refused)
/fail/timeout
Outgoing call that times out after 1 s (the called request needs 4 s)
504
Failed outgoing call after about 1 s, plus a separate slow request of about 4 s
/fail/random
Fails at random, ?rate=0.5 (default 50%)
500
Failure rate (%) of the service over time
/error
Always an unhandled error
500
Baseline failed request
/checkout?fail=1
Payment step declined by the payment service
402
Linked requests. 402 is not a server failure by default (see 6.4)


6.2 Trigger the failures
In the UI, click the red buttons under Failure scenarios. Click each one a few times. Start auto traffic also mixes in about one failure for every five calls.
Or use curl (Figure 9). Each call prints the response and the HTTP status.


Figure 9 - Failure endpoints - output from the application test run
6.3 Analyse the failures in Dynatrace
Open Distributed Tracing > Explorer and set the time range to the last 30 minutes. Failed calls are marked Error in red (Figure 10). Use the facets on the left to narrow the list to errors. If the Requests view is empty, switch to Spans.
Open a failed request, for example /fail/db. In the trace, find the span marked as failed. For /fail/db it is the SQL statement, for /fail/downstream the outgoing call, for /fail/exception the step validate_order (Figure 11).
Open the span details to see the request status (Failure), the span status (Error) and the HTTP status (Figure 12). Open the Exceptions tab to read the exception class and message (Figure 11).
Open the Logs tab of the trace to see log lines related to the failing request (compare Figure 7). Note: the trace and span IDs that the application writes into its own log lines were all zeros in this test, so do not rely on them for matching.
Open Services > SampleApp and check the failure rate and response time while /fail/random and the auto traffic are running.
For /fail/timeout, find the slow /slow request of about 4 s in the same trace and compare it with the 1 s wait of the caller.


Figure 10 - Spans view in Distributed Tracing: SampleApp spans, failed calls marked Error in red (failed SQL and server spans)


Figure 11 - Failed trace of /fail/db: the SQL span is marked failed and the Exceptions tab shows OperationalError - no such table: orders_archive


Figure 12 - Span details of a failed /fail/random request: request status Failure, span status Error, HTTP status 500
6.4 What counts as a failure
By default Dynatrace treats HTTP status codes 500 to 599 as server-side failures. Client errors such as 402 (payment declined) or 404 (not found) are not counted as server-side failures. So /checkout?fail=1 shows as a successful request with status 402 unless the rule is changed. The 5xx endpoints above are failures by default.
To count other codes as failures: Settings > Process and contextualize > Services > Failure detection rules, or for one service Services > Explorer > select the service > Actions > Settings > HTTP parameters, turn on override and add the range (for example 400-402). Menu names differ slightly between Dynatrace versions.
6.5 Failure test checklist
#
Check
Result
1
Each /fail/* endpoint returns the expected HTTP status (Figure 9)
[   ]
2
Failed requests appear in Distributed Tracing
[   ]
3
The failed span is identifiable (SQL, outgoing call or step) with its error text
[   ]
4
The log line of the failing request is linked in the trace
[   ]
5
Service failure rate rises while /fail/random runs
[   ]
6
402 on /checkout?fail=1 is understood as success unless the failure rule is changed
[   ]

Module 7 - Troubleshooting
Symptom
Likely cause / action
pip fails with "Permission denied" inside venv
The venv was created with sudo or by another user. Run: sudo chown -R $(whoami):$(whoami) venv, or delete it and recreate it as your own user.
curl: Failed to connect to port 8085
Application is not running in server mode (default for the older version was worker), it crashed, or the port is used by an old copy. Check: ss -tlnp | grep 8085, pkill -f sample_app.py, start again.
Starting fails with the bind step in the traceback
Port already in use (old process). Stop it or choose another port with --port.
"Could not load agent" / agent_state is not ACTIVE
OneAgent is not running on the host or the application was started before it was installed. Check the OneAgent service, then restart the application.
Run work / outgoing call is slow
The outgoing URL is unreachable and waits for the timeout. Set APP_URL to a reachable URL or lower APP_TIMEOUT.
Process visible but no service or traces
Traced code did not run yet (generate traffic), wrong time range, or agent not active. Wait a few minutes after the first requests.
Log shows agent_state=1 (TEMPORARILY_INACTIVE); logs and process arrive but no traces
The agent is not capturing for this process, so traced calls are dropped. Check that the host is in Full-stack (not Infrastructure-only) mode, that the process group is monitored and that the OneAgent service is healthy, then restart the application and confirm agent_state=0 (ACTIVE). The application logs the state only at startup, so check the last agent_state line in the log.
SQL spans missing
The SQL call is not inside another SDK trace. Wrap it in a traced endpoint or step.
402 or 404 not shown as failed
Expected - see 6.4. Only 5xx is a server-side failure by default.
Crash when the application stops
SDK not shut down. Stop it with Ctrl+C or a normal stop signal so oneagent.shutdown() runs.


For SDK debugging, temporarily set the environment variables DT_LOGLEVELSDK=finest and DT_LOGLEVEL=finest, restart, and review the OneAgent logs on the host. Remove them afterwards.
Final verification checklist
#
Check
Result
1
SDK check prints STATUS_INITIALIZED
[   ]
2
Application log shows agent_state=0 (ACTIVE)
[   ]
3
Health endpoint returns ok
[   ]
4
Process is visible in Dynatrace
[   ]
5
Service and endpoints appear in Distributed Tracing
[   ]
6
A trace shows request, internal step, outgoing call and SQL statements
[   ]
7
Failure module checks in 6.5 passed
[   ]

Rollback
Redeploy the previous application version (without the SDK code) and restart it. OneAgent process-level monitoring is not affected.
Removing the SDK needs no change to the OneAgent.
References
Dynatrace OneAgent SDK for Python: https://github.com/Dynatrace/OneAgent-SDK-for-Python
SDK API documentation: https://dynatrace.github.io/OneAgent-SDK-for-Python/
Dynatrace service failure detection: https://docs.dynatrace.com/docs/shortlink/service-failure-detection
