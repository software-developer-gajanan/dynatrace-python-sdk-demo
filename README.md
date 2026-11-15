# Dynatrace OneAgent SDK for Python – Application Tracing

A practical, step-by-step guide to integrating the Dynatrace OneAgent SDK for Python into a Python application, validating end-to-end tracing, testing failure scenarios, and troubleshooting common issues.

**Project:** Dynatrace OneAgent SDK for Python  
**Document ID:** SOP-DT-PY-002  
**Version:** 1.0  
**Author:** Gajanan Kalkundre  

---

## 1. Project Overview

Dynatrace OneAgent can detect Python processes and provide host-level and process-level monitoring. However, application-level services, endpoints, distributed traces, SQL calls, and outgoing requests require additional instrumentation.

This project demonstrates how to integrate the Dynatrace OneAgent SDK for Python to achieve application-level visibility and distributed tracing.

### Key Objectives

- Integrate the Dynatrace OneAgent SDK with a Python application.
- Enable application-level distributed tracing.
- Monitor incoming and outgoing requests.
- Capture SQL database calls.
- Monitor internal business transactions.
- Identify and troubleshoot application failures.
- Validate application performance through Dynatrace.

## 2. Architecture and Observability

The following architecture represents the monitoring flow:

```text
User / Browser
      |
      v
Python Application
      |
      +---- Incoming Request Tracing
      |
      +---- Custom Business Services
      |
      +---- SQL Database Calls
      |
      +---- Outgoing HTTP Requests
      |
      v
Dynatrace OneAgent
      |
      v
Dynatrace Platform
      |
      +---- Services
      +---- Endpoints
      +---- Distributed Tracing
      +---- SQL Spans
      +---- Failure Analysis
```

## 3. Prerequisites

| Component | Requirement |
|---|---|
| Dynatrace OneAgent | Installed and running in Full-Stack mode |
| OneAgent Version | 1.251 or newer |
| Python | CPython 3.8 or newer |
| Python SDK | oneagent-sdk |
| Operating System | Linux / Windows Server |
| Network | Application port 8085 accessible |
| Permissions | Normal user permissions |

**Note:** The SOP was tested on Linux. The production target is Windows Server. Alpine Linux using musl is not supported.

## 4. Python Environment Setup

### Linux

Create a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Upgrade the required packages:

```bash
python -m pip install --upgrade pip setuptools wheel
```

Install the Dynatrace SDK:

```bash
python -m pip install oneagent-sdk
```

Verify SDK initialization:

```bash
python -c "import oneagent; print(oneagent.initialize())"
```

**Expected Result:**

`STATUS_INITIALIZED` with `error=None`.

### Windows Server

```powershell
py -m venv venv
venv\Scripts\activate
py -m pip install oneagent-sdk
```

## 5. Run the Python Application

Start the sample application:

```bash
venv/bin/python sample_app.py --mode server --port 8085
```

Verify the application startup logs.

Expected SDK state:

```text
agent_state=0 (ACTIVE)
```

Check the health endpoint:

```bash
curl http://localhost:8085/health
```

Open the application:

```text
http://<server-ip>:8085/
```

### Application Configuration

| Environment Variable | Description |
|---|---|
| APP_URL | Target URL for outgoing HTTP calls |
| APP_TIMEOUT | Timeout configuration |
| APP_SERVICE_NAME | Service name in Dynatrace |
| APP_DB | Database configuration |
| APP_LOG | Application log configuration |

## 6. Verify Process Detection in Dynatrace

1. Open Dynatrace.
2. Navigate to **Compute → Processes**.
3. Search for the Python application process.
4. Open the process details.
5. Verify CPU, memory, and network metrics.

At this stage, process-level monitoring should be available.

## 7. Dynatrace SDK Instrumentation

The following SDK components are integrated into the Python application.

| Application Component | SDK Integration | Dynatrace Visibility |
|---|---|---|
| Import section | import oneagent | SDK dependency |
| init_sdk() | SDK initialization | Agent state |
| db() helper | trace_sql_database_request | SQL statements |
| http_get() helper | trace_outgoing_web_request | Outgoing requests |
| Business steps | trace_custom_service | Custom business transactions |
| Handler.traced() | trace_incoming_web_request | Incoming requests and endpoints |
| main() | oneagent.shutdown() | Clean SDK shutdown |

### Example: Custom Service Tracing

```python
with sdk.trace_custom_service("run_cycle", "SampleApp"):
    fetch_remote_data()
    store_records(5)
```

**Important:** SQL spans are recorded inside another SDK trace. Database calls should execute within a traced endpoint or business step.

## 8. Verify Distributed Tracing

Follow these steps in Dynatrace:

1. Generate application traffic using the application UI.
2. Open **Distributed Tracing → Explorer**.
3. Select the last 30 minutes.
4. Refresh the page.
5. Verify that the SampleApp service is visible.
6. Open an individual trace.
7. Verify incoming requests, internal business steps, outgoing calls, and SQL statements.

If the Requests view shows no matching requests, switch to the Spans view.

### Expected Visibility

- Application services
- Individual endpoints
- Request execution time
- Internal business transactions
- Outgoing HTTP calls
- SQL database statements
- Application failures

## 9. Application Failure Scenarios

The sample application contains dedicated endpoints to test different failure scenarios.

| Endpoint | Failure Scenario | Expected HTTP Status |
|---|---|---|
| /fail/exception | Exception inside business logic | 500 |
| /fail/db | SQL database error | 500 |
| /fail/downstream | Downstream service unavailable | 502 |
| /fail/timeout | Outgoing request timeout | 504 |
| /fail/random | Random application failure | 500 |
| /error | Unhandled application error | 500 |
| /checkout?fail=1 | Payment declined | 402 |

### Failure Analysis in Dynatrace

1. Open Distributed Tracing → Explorer.
2. Select the last 30 minutes.
3. Identify failed requests marked in red.
4. Open an individual failed request.
5. Identify the failed span.
6. Review the exception details.
7. Check the associated application logs.
8. Review service failure rate and response time.

### HTTP Failure Detection

By default, Dynatrace identifies HTTP status codes 500–599 as server-side failures.

HTTP 402 and 404 are not considered server-side failures by default.

Additional failure detection rules can be configured under:

**Settings → Process and contextualize → Services → Failure detection rules**

Menu names may vary depending on the Dynatrace version.

## 10. Troubleshooting

| Issue | Possible Cause / Resolution |
|---|---|
| Permission denied during pip installation | Check virtual environment ownership and permissions |
| Application not accessible on port 8085 | Verify application status and port availability |
| Could not load agent | Verify OneAgent installation and service status |
| Process visible but no traces | Verify SDK initialization and generate application traffic |
| agent_state=1 | Verify Full-Stack monitoring mode and OneAgent health |
| SQL spans missing | Ensure SQL calls execute inside another SDK trace |
| Outgoing calls are slow | Verify target URL and timeout configuration |
| Application crashes during shutdown | Ensure oneagent.shutdown() executes during clean shutdown |

### SDK Debugging

For troubleshooting, temporarily enable:

```bash
DT_LOGLEVELSDK=finest
DT_LOGLEVEL=finest
```

Restart the application and review OneAgent logs.

Remove the debugging settings after troubleshooting.

## 11. Final Verification Checklist

- [ ] Dynatrace OneAgent installed and running.
- [ ] Python SDK installed successfully.
- [ ] SDK initialization returns STATUS_INITIALIZED.
- [ ] Application startup shows agent_state=0 (ACTIVE).
- [ ] Application health endpoint is accessible.
- [ ] Python process is visible in Dynatrace.
- [ ] Application service is visible.
- [ ] Distributed traces are available.
- [ ] SQL statements are captured.
- [ ] Outgoing HTTP calls are captured.
- [ ] Failure scenarios are successfully validated.

## 12. Rollback Procedure

- Redeploy the previous application version without SDK instrumentation.
- Restart the application.
- OneAgent process-level monitoring will remain unaffected.
- Removing the SDK does not require uninstalling or modifying OneAgent.

## 13. References

- [Dynatrace OneAgent SDK for Python – GitHub](https://github.com/Dynatrace/OneAgent-SDK-for-Python)
- [Dynatrace OneAgent SDK – API Documentation](https://dynatrace.github.io/OneAgent-SDK-for-Python/)
- [Dynatrace Service Failure Detection](https://docs.dynatrace.com/docs/shortlink/service-failure-detection)

---

**Author:** Gajanan Kalkundre  
**Technology:** Python | Dynatrace | OneAgent SDK | Distributed Tracing  
