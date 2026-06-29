module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire op_start,
    input wire op_done,
    input wire cfg_regwen,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (op_start && op_done) |-> !$isunknown(cfg_regwen));

endmodule


bind keymgr sva_checker sva_checker_inst (.*);
