module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire sync_fault_d,
    input wire op_update_i,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (op_update_i) |-> $stable(sync_fault_d));

endmodule


bind keymgr_err sva_checker sva_checker_inst (.*);
