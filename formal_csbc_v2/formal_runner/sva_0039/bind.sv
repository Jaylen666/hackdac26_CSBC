module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire op_done_o,
    input wire op_update_o,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) !(op_done_o && op_update_o));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);
