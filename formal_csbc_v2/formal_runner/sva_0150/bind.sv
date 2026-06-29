module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire sync_fault_q,
    input wire op_done_i,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (sync_fault_q && op_done_i) |=> sync_fault_q);

endmodule


bind keymgr_err sva_checker sva_checker_inst (.*);
