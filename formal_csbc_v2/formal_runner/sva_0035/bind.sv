module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire op_ack,
    input wire adv_req,
    input wire dis_req,
    input wire op_err,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (op_ack) |-> (!$isunknown(adv_req) && !$isunknown(dis_req) && !$isunknown(op_err) && $stable(adv_req) && $stable(dis_req) && $stable(op_err)));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);
