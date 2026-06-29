module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire stage_sel,
    input wire adv_dvalid,
    input wire KeyMgrStages,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (stage_sel >= KeyMgrStages) |-> !adv_dvalid[stage_sel]);

endmodule


bind keymgr sva_checker sva_checker_inst (.*);
