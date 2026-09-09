// Follow references to an address (including pointer data) and decompile callers.
// @category Sonos

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

import java.util.ArrayDeque;
import java.util.HashSet;
import java.util.Set;

public class DecompileReferences extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 1) {
            throw new IllegalArgumentException("usage: DecompileReferences ADDRESS");
        }

        Address target = toAddr(arguments[0]);
        ArrayDeque<Address> pending = new ArrayDeque<>();
        Set<Address> visited = new HashSet<>();
        Set<Function> functions = new HashSet<>();
        pending.add(target);

        while (!pending.isEmpty()) {
            Address destination = pending.removeFirst();
            if (!visited.add(destination)) {
                continue;
            }
            println("references to " + destination + ":");
            for (Reference reference : getReferencesTo(destination)) {
                Address source = reference.getFromAddress();
                println("  " + source + " (" + reference.getReferenceType() + ")");
                Function function = getFunctionContaining(source);
                if (function != null) {
                    functions.add(function);
                } else if (currentProgram.getMemory().getBlock(source) != null &&
                        !currentProgram.getMemory().getBlock(source).isExecute()) {
                    pending.add(source);
                }
            }
        }

        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        for (Function function : functions) {
            println("\n===== " + function.getName() + " @ " + function.getEntryPoint() + " =====");
            DecompileResults result = decompiler.decompileFunction(function, 120, monitor);
            if (!result.decompileCompleted()) {
                println("decompilation failed: " + result.getErrorMessage());
                continue;
            }
            println(result.getDecompiledFunction().getC());
        }
        decompiler.dispose();
    }
}
