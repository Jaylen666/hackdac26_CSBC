module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire err_vld,
    input wire kmac_op_err_i,
    input wire op_done_i,
    input wire fault_o,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (err_vld && kmac_op_err_i) |-> ##[0:$] (op_done_i && fault_o[FaultKmacOp]));

endmodule


bind keymgr_err sva_checker sva_checker_inst (.*);
