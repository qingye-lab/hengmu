# Protocol session host

This service accepts a protocol request, stores active sessions in process
memory, and invokes the tool named by the request. Workers can be restarted
between protocol messages and requests arrive from several tenants.
