module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire value,
    input wire valid_chk,
    input wire clk,
    input wire rst_ni
);

assert property (@(posedge clk) disable iff (!rst_ni) $isunknown(value) |-> valid_chk(value) == 1'b0);

endmodule


bind keymgr_core sva_checker sva_checker_inst (.*);
