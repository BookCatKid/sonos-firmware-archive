// Print a headless Ghidra instruction listing for an inclusive address range.
// @category Sonos

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;

public class PrintInstructions extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 2) {
            throw new IllegalArgumentException("usage: PrintInstructions START END");
        }
        Address start = toAddr(arguments[0]);
        Address end = toAddr(arguments[1]);
        Instruction instruction = getInstructionAt(start);
        if (instruction == null) {
            disassemble(start);
            instruction = getInstructionAt(start);
        }
        while (instruction != null && instruction.getAddress().compareTo(end) <= 0) {
            println(instruction.getAddress() + "  " + instruction);
            instruction = instruction.getNext();
        }
    }
}
