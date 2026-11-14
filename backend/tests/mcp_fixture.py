from mcp.server.fastmcp import FastMCP
server=FastMCP('offline-fixture')
@server.tool()
def inspect_fixture(label:str)->str:
    """Return the supplied label, without network or filesystem access."""
    return 'fixture:'+label
if __name__=='__main__':server.run(transport='stdio')
