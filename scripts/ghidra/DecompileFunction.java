// Decompile the function containing an address, creating one at that address if needed.
// @category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompileFunction extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 1) {
            throw new IllegalArgumentException("usage: DecompileFunction ADDRESS");
        }

        Address address = toAddr(arguments[0]);
        Function function = getFunctionContaining(address);
        if (function == null) {
            disassemble(address);
            function = createFunction(address, null);
        }
        if (function == null) {
            throw new IllegalStateException("could not resolve function at " + address);
        }

        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        DecompileResults result = decompiler.decompileFunction(function, 120, monitor);
        if (!result.decompileCompleted()) {
            throw new IllegalStateException(result.getErrorMessage());
        }
        println("===== " + function.getName() + " @ " + function.getEntryPoint() + " =====");
        println(result.getDecompiledFunction().getC());
        decompiler.dispose();
    }
}
